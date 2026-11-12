# Emulator SIP GZM 3.0 🚍

Nieoficjalny edukacyjny emulator wyświetlacza i mapy linii ZTM Transport GZM.

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
- Kliknięcie przystanku: planowe odjazdy w ciągu 3 godzin; jeśli oficjalny GTFS-RT udostępni prognozę danego kursu — prognoza z etykietą **GTFS-RT live**.
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
