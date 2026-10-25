# SIP GZM Emulator (wersja demonstracyjna)

Responsywny emulator ekranu SIP i panelu sterowania na telefon. Pobiera rozkład GTFS ZTM GZM, a osobno próbuje odczytać pozycje pojazdów GTFS-Realtime.

## Uruchomienie lokalne

Wymagany Python 3.10+.

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Na komputerze otwórz `http://127.0.0.1:5000`. Aby użyć telefonu, komputer i telefon muszą być w tej samej sieci Wi-Fi; otwórz na telefonie lokalny adres IP komputera, np. `http://192.168.1.20:5000` (port 5000 musi być dozwolony w zaporze).

## Wdrożenie w chmurze (np. Render)

1. Wypakuj ZIP.
2. Wgraj zawartość do nowego repozytorium GitHub.
3. W Render wybierz **New → Web Service** i połącz repozytorium.
4. Build command: `pip install -r requirements.txt`
5. Start command: `gunicorn app:app`
6. Po wdrożeniu otwórz adres usługi na telefonie.

Pierwsze uruchomienie pobiera archiwum GTFS i może potrwać. Archiwum jest przechowywane w pamięci procesu przez 6 godzin.

## Własne trasy

W panelu „Własne trasy” można dodać linię testową, wpisać kierunek i listę przystanków (jeden wiersz na przystanek). Opcjonalnie podaj godzinę przed separatorem, np. `08:15 | Sosnowiec Dworzec`. Własna trasa pojawi się na liście linii z symbolem `★`; można ją obsługiwać przyciskami poprzedni/następny przystanek. Trasy są zapisywane w pliku `custom_routes.json` obok aplikacji. Na hostingu należy użyć trwałego dysku, jeśli trasy mają przetrwać ponowne wdrożenie lub restart środowiska.

## Dane i ograniczenia

- Rozkłady i przystanki: rozszerzony GTFS ZTM GZM, publiczny mirror:
  `https://github.com/TransportGZM-GTFS-mirror/TransportGZM-GTFS-extended-ver/releases/latest/download/TransportGZM-GTFS.zip`
- Pozycje pojazdów: `https://gtfsrt.transportgzm.pl:5443/gtfsrt/gzm/vehiclePositions`
- Dane o aktualizacjach kursów są publikowane jako osobny feed, ale ten prototyp nie używa ich jeszcze do obliczania opóźnień.
- Pozycje pojazdów są pobierane osobno i nie są automatycznie dopasowywane do wybranego kursu.
- Ręczne przyciski zmieniają ekran demonstracyjny, nie wpływają na rzeczywisty pojazd.
- Dane są udostępniane na warunkach licencji wskazanej przez publikującego; przy dalszym udostępnianiu zachowaj informację o źródle i licencji CC BY 4.0.
