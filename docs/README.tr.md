# Codex Routing Guard

Codex görevlerini doğru proje ve çalışma dizinine yönlendiren, desteklenen model/efor seçimini doğrulayan ve görev durumunu gerçek çalışma kanıtıyla izleyen açık kaynak araç ve skill.

Windows, Linux veya macOS ile Python 3.9+ gerekir; bakımı süren bir Python sürümü tercih et. Ek Python bağımlılığı yoktur. Windows'ta WSL veya Bash gerekmez. Proje içinde sohbet açmak için Codex uygulamasının ilgili sohbet/proje araçlarını sunması gerekir. CLI tek başına bu yeteneği sağlamaz.

```bash
git clone https://github.com/ns-koroglu/codex-routing-guard.git
cd codex-routing-guard
python3 install.py
python3 install.py --apply
python3 doctor.py
python3 -m unittest discover -v
```

İlk komut kurulum planını gösterir; `--apply` dosyaları yazar ve yedek oluşturur. Mevcut farklı dosyalar korunur. Model varsayılanları, hesap talimatları ve `AGENTS.md` değiştirilmez.

Windows PowerShell'de aynı komutları `python3` yerine `python` veya `py -3` ile çalıştır. Dosya kilitleri, Unicode yollar, özel erişim izinleri, RPC boruları ve kurtarma döngüsü platforma uygun çalışır. Windows geri dönüş yedeği önceki dosya erişim kurallarını da saklar. Gerçek test kapsamı [doğrulama matrisinde](validation.md) açıklanır.

Yeni bir Codex sohbetinde `$route-tasks` kullan. Yeni yürütme sohbeti açılmasını açıkça iste; skill mevcut görevin devamında ikinci bir sohbet açmaz. Gerçek host model/efor bilgisi yerel olarak alınmadan yönlendirme yapılmaz. Kurulum kimseye ek yetki vermez ve kotayı sıfırlamaz.

Görevin kalitesini artıracak uzmanlık eksikse beceri internette aday arayabilir. Önce mevcut becerileri kullanır; sonra görev uyumu, kaynak, bakım, lisans ve yıldız sayısını karşılaştırır. Varsayılan eşik depo başına 1.000 yıldızdır. Yıldız sayısı tek başına güvenlik veya kalite kanıtı değildir. Kullanıcı görevle ilgili beceri kurulumuna izin verdiyse yeniden onay istemeden; tam commit'e sabitlenmiş, içeriği incelenmiş ve hash'i doğrulanmış paketi kurabilir. İndirilen kod otomatik çalıştırılmaz; mevcut becerinin üzerine yazılmaz. Ağ erişilemiyorsa mevcut araçlarla işe devam eder. [Keşif ve kurulum sözleşmesi](../skills/route-tasks/references/skill-discovery.md), [rakip karşılaştırması](comparison.tr.md).

Recovery isteğe bağlıdır; otomatik etkinleşmez. Kararlı CLI, altı hook için ürün içi güven onayı ve gerçek doğrulama gerekir. [İngilizce ana rehber](../README.md), [kurulum ve geri dönüş](installation.md), [recovery](recovery.md) ve [doğrulama kapsamı](validation.md) ayrıntıları açıklar. Gerçek kota tükenmesi sonrası recovery bu açık kaynak sürümde henüz denenmemiştir.
