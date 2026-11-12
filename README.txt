EMULATOR SIP GZM — wersja 1.0
================================

To nieoficjalny, niekomercyjny prototyp emulatora tablicy pasażerskiej Transport GZM.

CO JEST W TEJ WERSJI
- responsywny interfejs telefonu i komputera;
- tablica SIP z żółto-czarnym numerem linii i szaro-żółtą listą przystanków;
- lista linii z oficjalnego GTFS, z zachowaniem osobnych route_id i wariantów tras;
- kierunki, warianty przebiegu i wybór kursu z rozkładu;
- czasy na tablicy pokazane jako narastające minuty od pierwszego przystanku (bez godzin na ekranie tablicy);
- mapa przystanków wybranej trasy oraz pozycje pojazdów z publicznego GTFS-RT, jeśli źródło odpowiada;
- dodawanie własnych linii, aktywowanie ich na tablicy, usuwanie, eksport i import JSON;
- automatyczne pobieranie aktualnego GTFS przy pierwszym uruchomieniu i przycisk aktualizacji;
- możliwość ręcznego wczytania ZIP z GTFS, gdy automatyczne pobranie nie zadziała.

PLIKI
- app.py — backend Python/Flask;
- templates/index.html — cały interfejs HTML, CSS i skrypt osadzony wewnątrz HTML;
- requirements.txt — biblioteki Python;
- run_windows.bat — uruchamianie w Windows;
- Procfile / render.yaml — konfiguracja wdrożenia na Render;
- data/ — lokalna baza GTFS i plik własnych linii tworzone automatycznie.

Nie ma osobnych plików .js. Kod obsługi interfejsu jest osadzony w index.html.

URUCHOMIENIE NA WINDOWS
1. Rozpakuj całe ZIP do jednego folderu.
2. Zainstaluj Python 3.11 lub nowszy z https://www.python.org/downloads/ i zaznacz Add Python to PATH.
3. Dwukrotnie kliknij run_windows.bat.
4. Przy pierwszym uruchomieniu program instaluje biblioteki i próbuje pobrać aktualny plik GTFS.
5. Otworzy się http://127.0.0.1:5000.
6. Aby korzystać z telefonu w tej samej sieci Wi-Fi, wpisz w telefonie adres IPv4 komputera z polecenia ipconfig, np. http://192.168.1.20:5000. Windows może zapytać o zezwolenie zapory — zezwól na dostęp w sieci prywatnej.

URUCHOMIENIE NA TELEFONIE PRZEZ RENDER
1. Wgraj zawartość folderu do nowego repozytorium GitHub.
2. W Render wybierz New → Blueprint i wskaż repozytorium.
3. Render odczyta render.yaml i zbuduje usługę.
4. Po wdrożeniu otwórz adres usługi na telefonie.

DANE I OGRANICZENIA
- Statyczne dane GTFS są pobierane najpierw z oficjalnego katalogu otwartych danych ZTM/Transport GZM (najnowszy dostępny ZIP). Jeśli katalog nie odpowie, aplikacja próbuje skorzystać z publicznego mirroru GitHub.
- Dane statyczne GTFS nie gwarantują informacji o nagłych objazdach ani wszystkich bieżących zmianach.
- Pozycje pojazdów pochodzą z publicznego GTFS Realtime. Nie są generowane ani udawane. Jeżeli kanał jest niedostępny, aplikacja pokaże jego status zamiast wymyślać pojazdy.
- Mapa używa kafelków OpenStreetMap i wymaga połączenia z internetem.
- Własne linie są zapisywane w data/custom_routes.json. Przed ponownym wdrożeniem lub przeniesieniem aplikacji pobierz JSON przez Eksportuj JSON, a potem wczytaj go przez Importuj JSON.
- Na darmowym hostingu pliki lokalne mogą zniknąć po ponownym wdrożeniu lub restarcie instancji. Eksport/import własnych linii służy do zachowania kopii.
- Aplikacja jest prototypem bez logowania. Nie wystawiaj publicznego panelu edycji, jeśli nie chcesz, aby inni dodawali lub usuwali własne linie.

LICENCJE I ŹRÓDŁA
- GTFS ZTM / Transport GZM: Creative Commons Attribution (CC BY 4.0); należy zachować informację o źródle.
- GTFS Realtime GZM: publiczny kanał opisany w katalogu otwartych danych.
- Dane mapowe: © OpenStreetMap contributors, ODbL.

Oficjalny GTFS:
https://otwartedane.metropoliagzm.pl/dataset/rozklady-jazdy-i-lokalizacja-przystankow-gtfs-wersja-rozszerzona

GTFS Realtime:
https://otwartedane.metropoliagzm.pl/dataset/rozklady-jazdy-i-lokalizacja-przystankow-gtfs-rt

To nie jest oficjalny produkt Transport GZM.
