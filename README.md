# CAN Gateway — Home Assistant (add-on + integracja)

Jedno repozytorium dla całego stacku Home Assistant CAN Gateway:

| Ścieżka | Opis |
|---------|------|
| `can_gateway/` | Dodatek Supervisor **v0.6.0** (USB-CAN, panel Ingress, REST API) |
| `can_gateway/integration/can_gateway_v3/` | Integracja bundlowana w dodatku (auto-deploy przy starcie) |
| `custom_components/can_gateway_v3/` | Ta sama integracja w korzeniu — **HACS** i ręczna instalacja |
| `hacs.json` | Manifest HACS (integracja z tego repo) |
| `repository.yaml` | Manifest sklepu dodatków Supervisor |

Firmware, konfigurator Windows i dokumentacja protokołu CAN: [can-control-suite](https://github.com/arturkmat/can-control-suite).

## Instalacja (Home Assistant OS / Supervised) — zalecane

1. **Ustawienia → Dodatki → Sklep dodatków → ⋮ → Repozytoria**
2. Dodaj: `https://github.com/arturkmat/ha-can-gateway`
3. Odśwież sklep, zainstaluj **CAN Gateway**, ustaw `can_port` / `can_bitrate`, uruchom dodatek.
4. **Gotowe** — dodatek kopiuje `can_gateway_v3` do `/config/custom_components/`, przeładowuje custom components i wysyła discovery Supervisor; HA tworzy wpis integracji automatycznie (`connection_mode=addon`).

Panel: **Ustawienia → Dodatki → CAN Gateway → Otwórz panel web** (Ingress) lub REST na porcie `8099`.

Opcjonalnie: w panelu **Skanuj magistralę** (`POST /api/scan`) — moduły w `/data/modules.json`.

## Instalacja integracji (HACS / direct serial)

Bez Supervisor lub gdy chcesz tylko integrację (port SLCAN w HA Core):

1. **HACS → Integracje → ⋮ → Własne repozytoria**
2. URL: `https://github.com/arturkmat/ha-can-gateway`, kategoria: **Integracja**
3. Pobierz **CAN Gateway v3**, zrestartuj HA.
4. **Ustawienia → Urządzenia i usługi → Dodaj integrację → CAN Gateway v3** — tryb **direct serial** lub **add-on** (jeśli dodatek działa na innym hoście).

Ręcznie: skopiuj `custom_components/can_gateway_v3` → `<config>/custom_components/`.

## API dodatku (skrót)

| Metoda | Endpoint | Opis |
|--------|----------|------|
| GET | `/api/health` | Health check |
| GET | `/api/discovery` | Zapisane moduły + metadane skanu |
| GET | `/api/modules` | Pełna lista modułów |
| POST | `/api/scan` | Skan magistrali + zapis do `/data` |
| GET | `/api/state` | Snapshot dla integracji v3 |
| POST | `/api/can/send` | TX CAN (usługi v3 w trybie add-on) |

Szczegóły encji, usług i migracji v2→v3: [can-control-suite/home_assistant/README.md](https://github.com/arturkmat/can-control-suite/blob/main/home_assistant/README.md) (dokumentacja protokołu i legacy v2).

## Sync integracji (dla deweloperów)

Po edycji `custom_components/can_gateway_v3/`:

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\sync_addon_integration.ps1
```

Kopiuje pliki do `can_gateway/integration/can_gateway_v3/` przed commitem / publikacją dodatku.

## Testy

```powershell
python -m pytest tests/
```
