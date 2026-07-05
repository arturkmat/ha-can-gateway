# CAN Gateway — dodatek Home Assistant Supervisor

Repozytorium zawiera **tylko** dodatek Supervisor (`can_gateway/` v0.5.1): USB-CAN (SLCAN / gs_usb), panel Ingress i REST API.

Integracja encji **CAN Gateway v3** jest w osobnym repozytorium: [can-control-suite](https://github.com/arturkmat/can-control-suite) (`custom_components/can_gateway_v3` lub HACS).

## Instalacja dodatku (Home Assistant OS / Supervised)

1. **Ustawienia → Dodatki → Sklep dodatków → ⋮ → Repozytoria**
2. Dodaj URL (musi być pełny adres GitHub, **nie** ścieżka katalogu):
   `https://github.com/arturkmat/ha-can-gateway`
3. Odśwież sklep dodatków.
4. Zainstaluj **CAN Gateway**, skonfiguruj port USB (`can_port`, `can_bitrate`, opcjonalnie `master_key_hex`) i uruchom dodatek.

Panel: **Ustawienia → Dodatki → CAN Gateway → Otwórz panel web** (Ingress) lub REST na porcie `8099`.

### Repo prywatne

Supervisor nie klonuje prywatnego GitHub bez tokenu. Opcje: Samba → `/addons/can_gateway/` (lokalnie) albo tymczasowo repo **Public** (bez sekretów w historii).

## Workflow v3.3 (dodatek + integracja v3)

1. Zainstaluj dodatek z tego repozytorium (kroki powyżej).
2. W panelu dodatku kliknij **Skanuj magistralę** (`POST /api/scan`). Moduły trafiają do `/data/modules.json` (metadane, FW, GPIO, rolety, relay).
3. Z [can-control-suite](https://github.com/arturkmat/can-control-suite) zainstaluj integrację **`can_gateway_v3`**:
   - HACS: dodaj repo `https://github.com/arturkmat/can-control-suite`, pobierz integrację **CAN Gateway v3**, **lub**
   - ręcznie: skopiuj `home_assistant/custom_components/can_gateway_v3` → `/config/custom_components/`.
4. Zrestartuj Home Assistant.
5. **Ustawienia → Urządzenia i usługi → Dodaj integrację → CAN Gateway v3** → połączenie **Add-on** (nie otwiera USB w Core — stany przez `/api/state` co ~5 s).

### API dodatku (skrót)

| Metoda | Endpoint | Opis |
|--------|----------|------|
| GET | `/api/health` | Health check |
| GET | `/api/discovery` | Zapisane moduły + metadane skanu |
| GET | `/api/modules` | Pełna lista modułów |
| POST | `/api/scan` | Skan magistrali + zapis do `/data` |
| GET | `/api/state` | Snapshot dla integracji v3 |
| POST | `/api/can/send` | TX CAN (usługi v3 w trybie add-on) |

## Integracja (encje, usługi, automatyzacje)

Dokumentacja platform, usług (`identify`, OTA, relay-link, LED bindings) i migracji v2→v3:  
[home_assistant/README.md w can-control-suite](https://github.com/arturkmat/can-control-suite/blob/main/home_assistant/README.md)

**Nie** dodawaj URL `can-control-suite` w **Repozytoriach sklepu dodatków** — to repo jest pod HACS / integrację, nie pod strukturę Supervisor add-on.