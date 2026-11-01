# Kaynak temelli beceri karşılaştırması

İnceleme tarihi: 1 Kasım 2026. Hedef: Codex görevini doğru çalışma alanına, yeteneğe ve desteklenen profile yönlendirmek; takip işlerini ve kesintileri güvenilir yönetmek. Prompt metni arayan `prompt-lookup` bu hedefin doğrudan rakibi değildir.

Bu çalışma, aşağıdaki projelerin yayımlanmış kaynak ve belgelerini karşılaştırır. Rakiplerin model kalitesi, gecikmesi veya maliyet iddiaları aynı koşullarda çalıştırılarak ölçülmedi. Dolayısıyla bir dünya sıralaması veya “internetteki en iyi” sonucu çıkarılmaz. Yıldız sayıları anlık depo metrikleridir; tekil becerinin kalitesini ölçmez.

## Karşılaştırılan projeler

| Proje | İncelenen yaklaşım | Routing Guard için çıkarım |
| --- | --- | --- |
| [pcx-wave/skill-router](https://github.com/pcx-wave/skill-router/tree/b3bb6a476094051472e0e1646fe83cc974e1e731) · 27 yıldız | Oturum içi küçük katalog; önerme/otomatik seçim; kullanım sayacı. Kaynak, Bash taramasına ve Claude beceri dizinine dayanıyor. | Hafif başlangıç bağlamı yararlı. Ancak kelime sayısına göre kısa görevleri dışlamak doğru değil; kısa bir güvenlik veya yayın isteği kritik olabilir. |
| [hussi9/skill-router](https://github.com/hussi9/skill-router/tree/5ab3734478e71d528710f85719a2ddc86e3b3633) · 31 yıldız | Hook tabanlı yerel indeks, belirsizlikte küçük model ve adımlara göre beceri/model yönlendirmesi. | Yetenek seçimini göreve bağlamak yararlı. Model adını talimata yazmak gerçek çalışan modelin doğrulanmasının yerini alamaz. Kendi gecikme/başarı iddiaları bu incelemede ölçülmedi. |
| [pym505/skill-router](https://github.com/pym505/skill-router/tree/8672a89c54f4df1df6fb5d5ff72fbf36fe8e2d7d) · 0 yıldız | Önce sunulan yerel katalog; en küçük gerekli beceri kümesi; salt okunur Codex tarayıcısı. | Görev için yeterli mevcut beceriyi kullanmak internet ve bağlam maliyetini azaltır. Her istekte beceri aramak gereksizdir. |
| [smart-route-skills/Skill-Router](https://github.com/smart-route-skills/Skill-Router/tree/b69d9adf8bc42d17d948b54c546088e822f57c22) · 1 yıldız | Manifest üzerinden alt göreve beceri seçimi, beceri içerik hash'i ve küçük görev paketleri. | Bir kez seçilmiş ve incelenmiş içeriği hash ile bağlamak tekrar tarama ve sessiz içerik değişimi riskini azaltır. Alt ajan kullanımı yine kullanıcı/host yetkisine bağlıdır. |
| [lomeshdutta/skill-router](https://github.com/lomeshdutta/skill-router/tree/4c538d8be575d5dee09a220c3304bd311ed97903) · 4 yıldız | Oturum niyeti, yerel envanter ve uygun beceri yoksa skills.sh keşfi; Jev ile seçim. | Yetenek eksikliğinde dış keşif yararlı. Bu sürümde keşif/kurulum için ek model veya servis bağımlılığı eklemek gerekmiyor. |
| [vercel-labs/skills — find-skills](https://github.com/vercel-labs/skills/tree/92a9a34ac3bf88e695e468b90c7bf32f163d32c8/skills/find-skills) · depo 37.548 yıldız | Görevden arama terimleri üretme, itibar/popülerlik kontrolü ve kullanıcı isteğiyle beceri kurma. | Keşif ve kurulum akışı alındı; yalnızca popülerliğe dayanmak yerine içerik incelemesi, tam commit, dosya hash'leri ve kaynak lisansını koruma eklendi. |

## Bu sürümde uygulananlar

1. **Gerçek platform taşınabilirliği:** Koşulsuz `fcntl`, Unix datagram soketi ve boru üzerinde `select` kaldırıldı. Windows'ta yerel byte kilidi ve DACL; Linux/macOS'ta POSIX kilidi ve dosya izinleri kullanılıyor. RPC, sınırlı kuyruklu stdio okuyucusuyla çalışıyor. Kurtarma döngüsü kalıcı durumu saniyede bir denetliyor.
2. **Mevcut doğrulama zincirini koruma:** Proje/checkout ayrımı, sabit görev kimliği, tekrar yönlendirme engeli, gerçek runtime/app kanıtı, kullanıcı durdurması, onay sınırları ve güçlü model incelemesi korunuyor. Yeni kaynak keşfi bu yetkileri genişletmiyor.
3. **İş için yararlı beceri keşfi:** Önce mevcut katalog, sonra somut eksiklik için internet. Varsayılan depo eşiği 1.000 yıldız; görev uyumu, bakım, lisans ve kaynak kimliği ayrıca inceleniyor. Bulunamayan aday özgün görevi durdurmuyor.
4. **İncelenmiş kurulum:** GitHub ref'i tam commit'e çözülüyor. Yalnızca seçilen beceri ve kaynak lisansları sınırlı bütçeyle indiriliyor. Tüm dosyaları kapsayan inceleme özeti kurulum sırasında tekrar doğrulanıyor. Kod çalıştırılmıyor; mevcut beceri değiştirilmeden korunuyor. Kurulum ile host tarafından keşfedilip gerçekten kullanılması ayrı raporlanıyor.
5. **Bakım ve maliyet:** Ek Python paketi, vektör veritabanı, LLM sınıflandırıcı veya servis gerekmiyor. Karmaşıklık ortak bir OS katmanı ve gerektiğinde okunan keşif referansında tutuluyor.

## Kanıt ve sınırlar

Yerel ve CI kontrol kapsamı [validation.md](validation.md) içinde tutulur. Gerçek public GitHub akışında `vercel-labs/skills/skills/find-skills` sabit commit'e indirilip incelendi ve izole Codex home'a kuruldu; indirilen kod çalıştırılmadı. Bu, gerçek uygulama sohbetinde otomatik beceri seçiminin veya model kalitesi artışının ölçümü değildir.

Başarılı testler; belirtilen durum ve güven sınırlarını doğrular. Tüm internetten daha iyi sonuç, model maliyeti tasarrufu, her hostta mevcut olmayan araçlar veya gerçek kota tükenmesinden sonra kusursuz devam iddiası üretmez. Bundan sonraki anlamlı kalite ölçümü, temsilî gerçek görevlerde aynı model/araç koşullarıyla kör değerlendirme ve kaydedilmiş sonuçların karşılaştırılmasıdır.
