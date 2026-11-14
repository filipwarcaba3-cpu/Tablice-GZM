# Emulator SIP GZM 4.1 — godziny SIP + własne przystanki na mapie

## Nowe w 4.1

- W wybranym rzeczywistym kursie GTFS **przed planowanym odjazdem** w dolnym pasku wyświetlacza SIP pokazuje się „Odjazd o HH:MM”. Po lewej przy czterech kolejnych przystankach jest ich planowana godzina (powyżej minut do przyjazdu).
- **Po rozpoczęciu kursu według rozkładu** tablica pokazuje również ostatnio minięte przystanki; zamiast minut dostają mały szary podpis „odjechał”. Widzimy maksymalnie cztery wiersze jednocześnie, więc w trakcie kursu widok przewija się wzdłuż trasy.
- Oznaczenia miniętych przystanków bez danych pozycyjnych/GTFS-RT są **wyliczone z planowego rozkładu**, a nie z rzeczywistej lokalizacji autobusu.
- Przyciski „Poprzedni”, „Następny” i „Symulacja” pozostają symulacją i są tak oznaczane, aby nie sugerować potwierdzonego przejazdu.
- W edytorze własnej linii wpisuj **`Nazwa przystanku | minuty`**, opcjonalnie **`| szerokość geograficzna | długość geograficzna`**. Przyciskiem **„📍 Wskaż na mapie”** można wybrać nazwany przystanek i kliknąć jego położenie na interaktywnej mapie. Koordynaty zostaną dopisane automatycznie.
- Własna linia działa i zapisuje się nawet przy braku współrzędnych. Przystanki bez współrzędnych nie mogą jednak być wyświetlane jako punkty na mapie. Trasa mapowa jest rysowana tylko na podstawie przystanków z lokalizacją.
- Import JSON akceptuje własne przystanki z oboma współrzędnymi lub bez obu; nie akceptuje pojedynczej współrzędnej.

### Instalacja GitHub + Render

Rozpakuj ZIP, wgraj **zawartość folderu** do głównego katalogu repozytorium GitHub, zachowując strukturę `templates`, `data`, `tests`.

Render:
- Build: `pip install -r requirements.txt`
- Start: `gunicorn --workers 1 --threads 8 --timeout 120 --bind 0.0.0.0:$PORT app:app`

Na Windows uruchom `start_windows.bat`.

**Uwaga:** informacje o godzinach wymagają załadowanego GTFS z potwierdzonym kursem. W podglądzie nieaktywnego wariantu trasy nie przypisujemy fikcyjnych godzin. Testy lokalne nie potwierdzają działania rzeczywistego GTFS na Twoim Renderze.

---

# Emulator SIP GZM 4.0 — tablica MKM, tryb jednej linii i lokalizacja

### Najważniejsze zmiany 4.0

- **Wybór linii przełącza mapę w tryb wybranej trasy**: usuwa skupiska i przystanki wszystkich innych linii, pokazuje wyłącznie przystanki wybranego kursu, przebieg linii oraz (jeśli dostępne) pojazdy GTFS-RT przypisane do tej linii. Wyczyszczenie linii przywraca mapę całej sieci.
- **Przycisk „◎ Moja lokalizacja”**: pokazuje pozycję urządzenia (niebieski punkt) i szacowany promień dokładności. Korzysta z `navigator.geolocation` dopiero po kliknięciu oraz zgodzie przeglądarki. Na Render wymaga HTTPS; pozycja nie jest wysyłana do serwera emulatora.
- **Tablica SIP**: nowe proporcje 645:306, mniejsza i węższa typografia, żółty nagłówek, osobny ZTM/data/zegar, pionowy czarny separator z małymi trójkątami, równe wiersze 4 przystanków i minut, jasnoszary pasek kierunku. Układ wzorowany na dostarczonej fotografii, nie twierdzimy, że czcionka jest dokładnie ta sama.
- **Wydajność i czytelność**: w trybie całej mapy pojazdy są nadal ukrywane przy dalszym oddaleniu. W trybie konkretnej linii pokazujemy jej pozycje nawet przy mniejszym zoomie, ponieważ liczba elementów jest znacznie mniejsza.
- **Bez fikcyjnych autobusów**: lokalizacja nie zastępuje danych GTFS-RT, a tablica rozkładowa nadal wymaga prawidłowo zaimportowanego GTFS. `data` i `templates` zachowują swoje nazwy oraz strukturę.

### Uruchamianie i wdrażanie

Wgraj **zawartość rozpakowanego folderu** do głównego katalogu repozytorium GitHub, zachowując `app.py`, `render.yaml`, `requirements.txt`, katalog `templates/` i `data/`. Na Render polecenie startowe to `gunicorn --workers 1 --threads 8 --timeout 120 --bind 0.0.0.0:$PORT app:app`, a build command `pip install -r requirements.txt`. Na Windows uruchom `start_windows.bat`.

Uwaga: testy przeglądarkowe były wykonane lokalnie na **testowym** GTFS z imitacją mapy (brak dostępu sieciowego do zewnętrznego CDN). Nie potwierdzają wczytania całej aktualnej sieci GZM ani GTFS-RT na Twoim Renderze. Jeśli `/api/status` nie ma `ready:true`, najpierw trzeba naprawić pobieranie/import rozkładów.

---

# Emulator SIP GZM 3.7 — poprawki list i wyświetlacza

- Każda kategoria linii sortowana jest naturalnie: 1, 2, 3, 10, 11, 100; M1, M2, M10, M103.
- Numer linii na tablicy SIP mieści się w lewej kolumnie (również M103).
- Gdy w żądanym przedziale dat GTFS brak aktywnych kursów, ale GTFS zawiera trasę, wyświetlacz może pokazać **podgląd zapisanego rzeczywistego wariantu**; **nie jest on rozkładem odjazdów na dziś**.
- Nadal wymagane jest prawidłowe źródło GTFS i import na Render; pozycje live nie są symulowane.

# Emulator SIP GZM 3.9 — aktualne daty i godziny kursów

## Zmiany 3.9 — lista kursów według polskiego zegara

- Domyślny rozkład korzysta z bieżącej daty i godziny `Europe/Warsaw` serwera. Kursy z wcześniejszej godziny, które już zakończyły przejazd, nie zajmują listy nadchodzących odjazdów.
- Działa także wykrywanie kursów będących w trasie oraz obliczenie kolejnego przystanku na podstawie planowych godzin GTFS. Jest to *symulacja rozkładowa*, nie gwarantowana lokalizacja pojazdu.
- Po północy przełączana jest data; kursy zapisane w GTFS godzinami `24:xx` / `25:xx` są przypisane do właściwej cywilnej daty odjazdu. Kurs rozpoczęty poprzedniego dnia może nadal być widoczny, gdy trwa w obecnym dniu.
- Co minutę wczytywane są aktualne kursy wybranej linii, o ile nie wybrano ręcznie innego kursu. Ręczny wybór jest zachowywany do zmiany linii/dnia.
- Górny panel ma osobny wybór linii, wariantu, kursu oraz przycisk „Wyczyść”. Ponowne kliknięcie wybranego numeru linii odznacza go.
- Pobieranie GTFS sprawdza, czy archiwum obejmuje **dzisiejszą** datę; nie wczytuje celowo przyszłego rozkładu jako aktualnego. Jeśli nie uda się zdobyć danych z obowiązującą datą, status informuje o brakujących danych; nie wymyśla odjazdów.
- Przy prezentacji podglądu trasy bez aktywnego kursu emulator oznacza ten widok jako **podgląd**, a nie dzisiejszy odjazd.

**Uwaga:** źródła GZM i sieć muszą odpowiadać, aby baza była gotowa. Nie ma gwarancji działania danych na żywo (GTFS-RT) przy awarii dostawcy. Render bez trwałego dysku może ponownie importować GTFS po uśpieniu usługi. Testy lokalne nie potwierdzają działania na zewnętrznym Render użytkownika.

## Historia wcześniejszych zmian

# Emulator SIP GZM 3.2 — poprawka pustej mapy i importu GTFS

Wersja 3.2: najpierw tworzy i udostępnia katalog przystanków i linii, a rozkłady importuje osobno; lżejszy import SQLite, postęp w `/api/status`, 4-godzinny cache pobranego GTFS na tej samej instancji. Odjazdy z przystanków i pełne trasy zaczną działać po ukończeniu importu stop_times. W razie ponownego restartu bez dysku trwałego na Render Free baza może być tworzona od nowa.

# Emulator SIP GZM 3.1 — poprawka pobierania GTFS 🚍

Nieoficjalny edukacyjny emulator wyświetlacza i mapy linii ZTM Transport GZM.

## Co poprawiono w 3.1

- Podczas pierwszego startu nie sprawdza kolejno siedmiu archiwów z długimi timeoutami: wybiera najnowszy zasób GTFS oraz źródło zapasowe.
- Pobieranie ma krótsze timeouty, a w razie błędu kolejna próba następuje po około 90 sekundach (zamiast 15 minut).
- Tworzenie indeksów SQLite przesunięto na koniec importu danych, co może znacząco skrócić pierwsze przetwarzanie.
- Ekran wyświetla etap ładowania i ewentualny błąd.
- Opcjonalna zmienna środowiskowa `SIP_GTFS_URL` przyjmuje bezpośredni **https** URL do własnego, kompletnego archiwum GTFS. Możesz ją ustawić w Render → Environment.
- Dodatkowa opcja `SIP_RETRY_SECONDS` (domyślnie 90) steruje odstępem ponownych prób.

**Uwaga:** nie potwierdzono pobrania najnowszych danych z sieci w środowisku testowym; dostępność zewnętrznych serwerów i czas parsowania na Render zależą od źródła, rozmiaru bazy i limitów usługi.

## Wgrywanie ZIP na GitHub → Render

1. Rozpakuj archiwum ZIP. **Wgraj pliki znajdujące się w archiwum do głównego katalogu repozytorium** (tak, aby `app.py` był w katalogu głównym).
2. Wejdź na GitHub → swoje repozytorium → **Add file → Upload files** i dodaj `app.py`, `requirements.txt`, `Procfile`, `render.yaml`, `.python-version`, cały folder `templates` oraz pozostałe pliki.
3. W Render wybierz **New → Web Service**, podłącz repozytorium.
4. **Build Command:** `pip install -r requirements.txt`
5. **Start Command:** `gunicorn --workers 1 --threads 8 --timeout 120 --bind 0.0.0.0:$PORT app:app`
6. Health Check Path: `/health`. Po wdrożeniu otwórz adres usługi (strona jest pod `/`).

Możesz też użyć Render Blueprint (`render.yaml`), jeśli masz taką opcję na koncie. Dostępność planu Free zależy od Render.

## Uruchamianie Windows

1. Zainstaluj Python 3.11 lub nowszy.
2. Rozpakuj ZIP i kliknij dwukrotnie `start_windows.bat`. Skrypt automatycznie instaluje biblioteki, uruchamia `app.py` i otwiera `http://127.0.0.1:5000`.
3. Alternatywnie: `pip install -r requirements-windows.txt`, później `python app.py`.

## Funkcje

- Mapa przystanków GZM w widocznym obszarze: skupiska przy oddaleniu, szczegółowe przystanki po przybliżeniu.
- Kliknięcie przystanku: planowe odjazdy w ciągu 12 godzin; jeśli oficjalny GTFS-RT udostępni prognozę danego kursu — prognoza z etykietą **GTFS-RT live**.
- Kliknięcie odjazdu: otwarcie właściwego kursu, trasy i przystanków.
- Lista wszystkich **autobusowych** linii znajdujących się w pobranym GTFS, warianty i kursy aktywne w dniu przeglądania.
- Aktualizowane znaczniki autobusów **tylko jeśli odpowiada oficjalny GTFS-RT**; brak danych jest jasno oznaczany.
- Tablica pokładowa SIP na wzór przesłanej fotografii: kolejny przystanek i narastające minuty dla następnych przystanków.
- Edycja własnych linii i przystanków, lokalny zapis, import/eksport JSON (własne linie nie udają oficjalnych GZM).
- `/health` i automatyczne odświeżanie GTFS po wygaśnięciu daty danych.

## Ważne ograniczenia

- ZIP **nie zawiera wbudowanego aktualnego rozkładu**. Pobiera go po starcie z [oficjalnego katalogu GZM](https://otwartedane.metropoliagzm.pl/dataset/rozklady-jazdy-i-lokalizacja-przystankow-gtfs-wersja-rozszerzona) lub zapasowego, publicznego mirrora. Pierwsze pobieranie i przetwarzanie mogą potrwać. Jeśli źródło nie odpowiada, aplikacja pokazuje błąd — nie tworzy fałszywych linii.
- GZM GTFS-RT: https://gtfsrt.transportgzm.pl:5443/gtfsrt/gzm/vehiclePositions oraz https://gtfsrt.transportgzm.pl:5443/gtfsrt/gzm/tripUpdates. Nie ma gwarancji dostępności każdego autobusu ani prognozy każdej linii.
- Odległe zbliżenie mapy pokazuje skupiska zamiast każdego pojedynczego punktu (wydajność). Wszystkie przystanki w widoku są dostępne po przybliżeniu.
- Czerwona trasa to połączenie współrzędnych kolejnych przystanków, **nie** dokładna geometria ulic czy ruch autobusu po drogach.
- Render Free może wyłączyć proces po bezczynności. Dane w lokalnym pliku SQLite są nietrwałe na instancji bez trwałego dysku; po ponownym uruchomieniu program może znowu pobrać GTFS. Własne linie są zapisywane **w przeglądarce użytkownika**, a nie na serwerze.
- Lokalizacja i odjazdy mogą się różnić od oficjalnych wyświetlaczy; sprawdź komunikaty ZTM w razie podróży.

## Źródła i licencje

- [ZTM/Metropolia GZM — GTFS](https://otwartedane.metropoliagzm.pl/dataset/rozklady-jazdy-i-lokalizacja-przystankow-gtfs-wersja-rozszerzona), CC BY 4.0.
- [GZM GTFS-RT](https://www.otwartedane.metropoliagzm.pl/dataset/rozklady-jazdy-i-lokalizacja-przystankow-gtfs-rt), CC BY 4.0.
- [OpenStreetMap](https://www.openstreetmap.org/copyright), ODbL; mapa Leaflet.js z CDN.
- Projekt jest niezależny, nieoficjalny i edukacyjny.

## Testy

Lokalnie: `python -m unittest discover -s tests -v`. Testy tworzą tymczasowy sztuczny GTFS wyłącznie do testowania i **nie są wyświetlane jako rzeczywiste dane GZM**.

## Poprawka tablicy 3.1
Wyświetlacz dopasowano do zdjęcia ZTM: numer linii, czarny separator ze strzałkami, żółty nagłówek, cztery kolejne przystanki i narastające minuty, szary pasek kierunku. Interfejs skaluje się na telefonie i PC.

## Poprawka 3.3 — tablice przystankowe i wybór kursów
- Tablice przystankowe szukają rozkładowych odjazdów do 12 godzin naprzód zamiast 3, a gdy rozkłady są jeszcze importowane informują o tym jednoznacznie.
- Dla linii bez kursów w wybranym dniu pokazany może być najbliższy dzień kursowania w zakresie kolejnych 7 dni; jego data jest oznaczona, nie jest przedstawiana jako dzisiaj.
- Przyspieszono pobieranie wariantów linii przez jedno zapytanie o przystanki na trasie zamiast osobnego zapytania dla każdego aktywnego kursu.
- Żadne z tych ulepszeń nie zastępuje pomyślnego importu GTFS. Dopóki `/api/status` zwraca `ready:false`, rzeczywiste odjazdy nie są dostępne. Nie są generowane fikcyjne kursy ani pojazdy.
