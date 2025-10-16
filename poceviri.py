import os
import time
import re
import polib
import json 
from google import genai
from google.genai.errors import APIError
from typing import List, Dict, Any 

# --- API ANAHTARLARI VE İSTEMCİ YÖNETİMİ ---
API_KEYS = [
    # Bu kısmı doldurmalısınız!
    "AIzaSyAdcdT89H3bCOvDYAtMxhAv29QW3UWUsjo",
    "AIzaSyCrsTVZ7xNh6flE6gQDtHS9n1Ky919VqLc",
]

class GeminiClientManager:
    """Çoklu API anahtarlarını yönetir ve Resource Exhausted hatalarında anahtar değiştirir."""
    def __init__(self, api_keys):
        self.api_keys = api_keys
        self.current_key_index = 0
        self.client = self._initialize_client()

    def _initialize_client(self):
        """Mevcut dizindeki anahtarla bir Gemini istemcisi başlatır."""
        if not self.api_keys:
            raise ValueError("API anahtar listesi boş olamaz.")
        
        current_key = self.api_keys[self.current_key_index]
        print(f"\n[CLIENT] Yeni anahtar yükleniyor (Index: {self.current_key_index + 1}/{len(self.api_keys)}).")
        try:
            return genai.Client(api_key=current_key)
        except Exception as e:
            raise Exception(f"Gemini istemcisi başlatılırken hata: {e}")

    def switch_client(self):
        """Sıradaki API anahtarına geçer ve istemciyi yeniden başlatır."""
        self.current_key_index += 1
        if self.current_key_index >= len(self.api_keys):
            print("\n[KRİTİK HATA] Tüm API anahtarları tükenmiştir veya limitlerine ulaşmıştır.")
            raise StopIteration("Tüm API anahtarları tükendi.")
            
        self.client = self._initialize_client()
        return self.client

    def get_client(self):
        """Mevcut istemciyi döndürür."""
        return self.client

# --- BAŞLANGIÇ KONTROLLERİ ---
print(">>> BETİK BAŞLADI! Kütüphaneler başarıyla yüklendi. <<<")

try:
    client_manager = GeminiClientManager(API_KEYS)
    client = client_manager.get_client()
    
    # JSON ÇIKTISI İÇİN ZORUNLU KONFİGÜRASYON
    JSON_CONFIG = genai.types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema={
            "type": "array",
            "items": {"type": "string"},
            "description": "Gönderilen her bir metin için karşılık gelen Türkçe çeviri listesi."
        }
    )
except StopIteration as e:
    print(f"Betik durduruldu: {e}")
    exit(1)
except Exception as e:
    print(f"Başlangıç hatası: {e}")
    exit(1)


# Yapılandırma
MODEL_NAME = 'gemini-2.5-flash'  
SOURCE_LANG = 'English'
TARGET_LANG = 'Turkish'
# 🚨 DÜZELTME: Güvenli limit 1500 tokene düşürüldü.
MAX_BATCH_TOKENS = 5000 

CHAR_TO_TOKEN_RATIO = 4.0 
MAX_BATCH_CHARS = int(MAX_BATCH_TOKENS * CHAR_TO_TOKEN_RATIO) 
SEPARATOR = "---[END_DELIM_7B3E]----" 
DELAY_BETWEEN_CALLS = 2

# Değişken ve özel etiket kalıbı
VARIABLE_PATTERN = re.compile(r'(\{[\w._>%-]+\}|\[/?[\w/]+\])') 

# --- ÇEVİRİ FONKSİYONU (JSON TABANLI) ---
def translate_batch_with_gemini(text_batch, client_mgr: GeminiClientManager):
    """Metinleri toplu olarak gönderir, Listeleme hatasında sonsuza kadar tekrar dener."""
    
    full_text = json.dumps(text_batch, ensure_ascii=False)
    
    prompt = (
        f"Aşağıdaki {len(text_batch)} adet metin dizisini ('{SOURCE_LANG}' dilinde) Türkçe'ye çevir. "
        "Çeviri sırasında süslü parantez içindeki (örneğin: {HIZ}) ve köşeli parantez içindeki (örneğin: [bulletlist]) "
        "değişkenleri, etiketleri veya özel kodları **kesinlikle çevirme ve aynen koru**. "
        "Yanıtı, şemada belirtilen formata (bir JSON listesi) kesinlikle uygun olarak döndür. "
        "JSON listesindeki her eleman, çevrilen metne karşılık gelmelidir. "
        f"Çevrilecek Metin Dizisi: {full_text}"
    )

    max_retries_api = 5 
    json_retry_count = 0 

    while True: # BAŞARANA KADAR TEKRAR DENE
        json_retry_count += 1
        
        for api_attempt in range(max_retries_api):
            try:
                current_client = client_mgr.get_client()
                response = current_client.models.generate_content(
                    model=MODEL_NAME,
                    contents=prompt,
                    config=JSON_CONFIG
                )
                
                raw_response_text = response.text.strip()
                
                if raw_response_text.startswith("```json"):
                    raw_response_text = raw_response_text[7:]
                if raw_response_text.endswith("```"):
                    raw_response_text = raw_response_text[:-3]

                translated_texts = json.loads(raw_response_text.strip())
                
                if len(translated_texts) != len(text_batch):
                    raise ValueError("Liste boyutları eşleşmiyor.")
                
                return translated_texts # Başarılı dönüş
                
            except ValueError:
                # Liste boyutları hatası (Dış döngü yakalayacak)
                raise
            
            except (APIError, json.JSONDecodeError) as e:
                # API veya JSON Format hatası
                is_resource_exhausted = isinstance(e, APIError) and ('Resource Exhausted' in str(e) or '429' in str(e))
                
                if is_resource_exhausted and client_mgr.current_key_index < len(client_mgr.api_keys) - 1:
                    print(f"\n[API KOTA HATASI] Anahtar {client_mgr.current_key_index + 1} kotası doldu.")
                    client_mgr.switch_client()
                    time.sleep(5) 
                    continue 
                
                wait_time = 2 ** (api_attempt + 1)
                if api_attempt < max_retries_api - 1:
                    print(f"\n[HATA] Deneme {api_attempt + 1}: Hata: {type(e).__name__}. {wait_time} saniye bekleniyor...")
                    time.sleep(wait_time) 
                    continue
                else:
                    raise e # API hataları için son denemeden sonra hata fırlat
            except StopIteration:
                raise
            except Exception as e:
                raise e

            except ValueError as e:
            # Liste boyutları hatası (Sonsuz döngü burada yakalar ve tekrar dener)
             print(f"\n[JSON BOYUT HATASI] Boyut eşleşmedi. Toplam Deneme {json_retry_count}. 3 saniye beklenip Batch tekrar deneniyor...")
            time.sleep(3)
            continue # Başarılı olana kadar tekrar dene

def automate_po_translation(input_filepath, output_filepath):
    """PO dosyasını akıllıca okur, çevirir ve toplu çeviri yapar."""
    
    file_to_load = input_filepath
    if os.path.exists(output_filepath):
        print(f"[AKILLI YÜKLEME] '{output_filepath}' (TR.po) dosyası bulundu. Kaldığı yerden devam etmek için bu dosya yüklenecektir.")
        file_to_load = output_filepath
        
    try:
        po = polib.pofile(file_to_load) 
    except Exception as e:
        # Hata olursa dış döngüye fırlat
        raise Exception(f"PO Dosya Okuma Hatası: {e}")

    print(f"'{file_to_load}' dosyasında {len(po)} adet giriş bulundu.")
    
    entries_to_translate = []
    texts_to_translate = []
    total_skipped = 0
    
    # Adım 1: Çevrilecek Girişleri Topla (autotranslated bayraklı olanlar atlanır)
    for entry in po:
        if not entry.msgid:
            continue
            
        if 'autotranslated' in entry.flags:
             total_skipped += 1
             continue
             
        if entry.obsolete or 'fuzzy' in entry.flags:
            continue
            
        text_to_translate = entry.msgstr.strip()
        if not text_to_translate:
            continue
        
        entries_to_translate.append(entry)
        texts_to_translate.append(text_to_translate)
    
    if total_skipped > 0:
        print(f"=====================================================")
        print(f"| {total_skipped} adet giriş, 'autotranslated' bayrağı olduğu için atlandı (Hata sonrası devam).")
        print(f"=====================================================")
        
    if not entries_to_translate:
        print("Çevrilecek yeni bir giriş bulunamadı.")
        # Programı başarıyla bitir
        return

    total_new_entries = len(entries_to_translate)
    print(f"Toplam {total_new_entries} adet yeni giriş çevrilmeye hazır.")
    
    # Adım 2: Batchleri YEREL OLARAK Hesaplama (Karakter tabanlı gruplama)
    all_batches = []
    current_batch_entries = []
    current_char_count = 0
    total_tokens_count = "HESAPLANAMADI"
    
    # Token Sayımı (Sadece Bilgi Amaçlı)
    try:
        total_tokens_count = client_manager.get_client().models.count_tokens(
            model=MODEL_NAME,
            contents=[str(texts_to_translate)] 
        ).total_tokens
    except Exception:
        pass

    # YEREL BATCHLEME DÖNGÜSÜ
    for i in range(total_new_entries):
        text = texts_to_translate[i]
        entry = entries_to_translate[i]
        
        entry_char_count = len(text) + 5
        
        if (current_char_count + entry_char_count) > MAX_BATCH_CHARS and current_batch_entries:
            all_batches.append(current_batch_entries)
            current_batch_entries = []
            current_char_count = 0
            
        current_batch_entries.append(entry)
        current_char_count += entry_char_count

    if current_batch_entries:
        all_batches.append(current_batch_entries)
        
    
    # Ortalama Token Çıktısı (Final)
    if total_tokens_count != "HESAPLANAMADI":
        average_tokens = total_tokens_count / total_new_entries
    else:
        average_tokens = "N/A"
        
    estimated_time = len(all_batches) * (DELAY_BETWEEN_CALLS + 10) 
    
    print(f"=====================================================")
    print(f"| TOPLAM ÇEVİRİ BATCH SAYISI: {len(all_batches):,}")
    print(f"| TOPLAM GİRDİ TOKENI (Yaklaşık): {total_tokens_count:,}")
    print(f"| ORTALAMA TOKEN/GİRİŞ: {average_tokens}")
    print(f"| Her toplu çağrı {MAX_BATCH_TOKENS:,} tokene kadar gönderilecektir (Karakter tabanlı gruplandı).")
    print(f"| TAHMİNİ SÜRE: ~{estimated_time // 60} dakika {estimated_time % 60} saniye")
    print(f"=====================================================")


    # Adım 3: Çeviri Başlatılıyor
    total_translated = 0
    
    for batch_index, batch_entry_list in enumerate(all_batches):
        
        current_batch_texts = [entry.msgstr.strip() for entry in batch_entry_list]
        current_batch_number = batch_index + 1
        
        print(f"\n[İŞLENİYOR] Batch {current_batch_number}/{len(all_batches)} - {len(current_batch_texts)} giriş çevriliyor...")
        
        try:
            translated_batch = translate_batch_with_gemini(current_batch_texts, client_manager)
            
            # Çevirileri orijinal girişlere ata
            for j, entry in enumerate(batch_entry_list):
                entry.msgstr = translated_batch[j]
                entry.flags.append('autotranslated') 
                total_translated += 1
            
            # HER BAŞARILI BATCH SONRASI DİSKE KAYIT
            po.save(output_filepath)
            print(f"[BAŞARILI KAYIT] Batch {current_batch_number} başarıyla 'TR.po' dosyasına kaydedildi. Toplam çevrilen: {total_translated + total_skipped}")
            
            time.sleep(DELAY_BETWEEN_CALLS) 
            
        except StopIteration as e:
            print(f"\n[KRİTİK HATA] Tüm API anahtarları tükendi. {total_translated + total_skipped} girişten sonra betik durduruldu.")
            raise # Ana döngüye fırlatılır
        except Exception as e:
            # Hata durumunda (Kalıcı JSON hatası dahil), detaylı raporlama yapılır ve döngüye fırlatılır.
            start_entry = total_skipped + 1 + (batch_index * len(batch_entry_list)) 
            end_entry = start_entry + len(batch_entry_list) - 1
            
            print(f"\n[ÇÖKME RAPORU] Batch {current_batch_number} çevirisi {e} hatası nedeniyle başarısız oldu.")
            print(f"!!! KRİTİK HATA ALANI: Girişler {start_entry} - {end_entry} arasında yer alıyor.")
            raise # Ana döngüye fırlatılır

    print(f"\nİşlem tamamlandı. Toplam {total_translated + total_skipped} giriş toplu olarak çevrildi.")
    
    print(f"Çevrilmiş dosya '{output_filepath}' olarak kaydedildi.")

# --- KULLANIM ---
if __name__ == "__main__":
    INPUT_FILE = 'EN1.po' 
    OUTPUT_FILE = 'TR.po' 
    
    # Başarısızlıkta yeniden deneme için ana döngü
    crash_count = 0
    
    while True:
        try:
            if crash_count > 0:
                print("\n\n#####################################################")
                print(f"!!! YENİDEN BAŞLATILIYOR (Tekrar Deneme Sayısı: {crash_count}) !!!")
                print("#####################################################")
                time.sleep(5) # Yeniden başlamadan önce 5 saniye bekle
            
            # Ana çeviri fonksiyonunu çağır
            automate_po_translation(INPUT_FILE, OUTPUT_FILE)
            
            # Eğer fonksiyon hatasız tamamlanırsa döngüden çık
            print("\n\n*** ÇEVİRİ İŞLEMİ BAŞARIYLA TAMAMLANDI! ***")
            break
            
        except StopIteration:
            # Tüm API anahtarları tükendiğinde döngüyü kır
            print("\n\n*** TÜM API ANAHTARLARI TÜKENDİ. BETİK SONLANDIRILDI. ***")
            break
            
        except Exception as e:
            # Genel bir hata oluştuğunda (ÇÖKME RAPORU'ndan sonra buraya düşer)
            crash_count += 1
            print(f"\n[ANA KONTROL] Program beklenmedik bir hata ile durdu: {type(e).__name__}.")
            print("Çeviri durumu kaydedildi. Yeniden başlatılıyor...")
            # 'while True' döngüsü, betiği baştan başlatır (TR.po'yu yükleyerek devam eder).