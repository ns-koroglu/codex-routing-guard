# Codex Routing Guard

Codex görevlerini doğru proje ve çalışma dizinine yönlendiren, desteklenen model/efor seçimini doğrulayan ve görev durumunu gerçek çalışma kanıtıyla izleyen açık kaynak araç ve skill.

macOS/Linux ve Python3.9+ gerekir. Ek Python bağımlılığı yoktur. Proje içinde sohbet açmak için Codex uygulamasının ilgili sohbet/proje araçlarını sunması gerekir. CLI tek başına bu yeteneği sağlamaz. Windows yerel desteği yoktur.

```bash
git clone https://github.com/ns-koroglu/codex-routing-guard.git
cd codex-routing-guard
python3 install.py
python3 install.py --apply
python3 doctor.py
python3 -m unittest discover -v
```

İlk komut kurulum planını gösterir; `--apply` dosyaları yazar ve yedek oluşturur. Mevcut farklı dosyalar korunur. Model varsayılanları, hesap talimatları ve `AGENTS.md` değiştirilmez.

Yeni bir Codex sohbetinde `$route-tasks` kullan. Yeni yürütme sohbeti açılmasını açıkça iste; skill mevcut görevin devamında ikinci bir sohbet açmaz. Gerçek host model/efor bilgisi yerel olarak alınmadan yönlendirme yapılmaz. Kurulum kimseye ek yetki vermez ve kotayı sıfırlamaz.

Recovery isteğe bağlıdır; otomatik etkinleşmez. Kararlı CLI, altı hook için ürün içi güven onayı ve gerçek doğrulama gerekir. [İngilizce ana rehber](../README.md), [kurulum ve geri dönüş](installation.md), [recovery](recovery.md) ve [doğrulama kapsamı](validation.md) ayrıntıları açıklar. Gerçek kota tükenmesi sonrası recovery bu açık kaynak sürümde henüz denenmemiştir.
