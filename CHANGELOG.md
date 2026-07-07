# Changelog — ha-can-gateway

## 2026-07-07 (add-on v0.7.2 + integration v3.5.2)

### fix: module names, strict entity catalog, Ingress entity table

- **`GET_MODULE_NAME` (cmd 37):** odczyt chunked (offset 0,3,6,…) w `configurator_engine`, `deep_config`, `module_service` — nazwy modułów w `modules.json` i panelu Ingress.
- **`entity_export.py`:** przekaźniki tylko z przypisanych ról GPIO / `relay_pulse_ms` / MCP role dump; wykluczenie par shutter; bez slotów z `relays=N` w GET_SUMMARY ani z pasywnej telemetrii 0x600.
- **`deep_config`:** `read_gpio_roles_from_module()` podczas deep refresh po skanie.
- **`bus_manager` / `configurator_engine`:** usunięty fallback 1..16 przekaźników gdy brak przypisań.
- **Panel Ingress:** rozwijana tabela encji per moduł z `GET /api/entities`.
- **Integracja v3.5.2:** katalog encji z dodatku bez fantomowych switchy; nazwa urządzenia z `module.name`.
- **Testy:** `test_entity_export` — shutter-only, stale summary, module name.

## 2026-07-07 (add-on v0.7.1 + integration v3.5.1)

### fix: empty Ingress panel + phantom HA entities before add-on scan

- **Panel Ingress:** ścieżki statyczne `static/app.js` i `static/styles.css` względne (Ingress HA nie obsługuje `/static/...` od roota); baner błędów API; komunikaty „brak skanu” / błąd skanu.
- **`bus_manager`:** `GET /api/entities` i `GET /api/discovery` nie syntetyzują encji z live runtime gdy brak `/data/entities.json` (`discovery_version=0`); liczniki encji per moduł z zapisanego katalogu.
- **`can_gateway_v3` (add-on mode):** encje HA tworzone wyłącznie gdy `discovery_version > 0` i `entity_count > 0`; status integracji `waiting` do pierwszego skanu w panelu dodatku; przeładowanie platform tylko po zmianie `discovery_version` z gotowym katalogiem.
- **README:** procedura usunięcia starej integracji i ponownego dodania po skanie.
- **Testy:** `test_addon_api_catalog.py`, rozszerzone `test_can_gateway_v3_addon_sync`.

## 2026-07-07 (add-on v0.7.0 + integration v3.5.0)

### fix: unified add-on ↔ integration architecture (catalog is source of truth)

- **Add-on entity catalog:** po skanie + deep read zapis `/data/entities.json` (razem z `modules.json`, wspólny `discovery_version`).
- **`entity_export.py`:** tylko encje przypisane w FW (GPIO roles, mapy relay/shutter, sensory) — bez hipotetycznych slotów z samych liczników GET_SUMMARY.
- **REST API:** `GET /api/entities` (katalog + live values), `GET /api/discovery` (moduły + encje + `discovery_version`), `POST /api/scan` zwraca `entity_count`.
- **`can_gateway_v3` (add-on mode):** polling `/api/discovery` + `/api/entities` zamiast `/api/state`; przeładowanie platform HA przy zmianie `discovery_version`; brak duplikacji tworzenia encji w `addon_sync`.
- **Panel Ingress:** kolumna liczby encji per moduł, status katalogu z `/api/discovery`.
- **Testy:** rozszerzone `test_module_store`, `test_entity_export`, `test_can_gateway_v3_addon_sync`.

## 2026-07-05 (v0.6.2)

### fix: V3 plain CAN — TX nie blokowany, skan/auto_scan bez master_key, Ingress UI

- **`configurator_bridge`:** `prepare_outgoing_frames` przekazuje `secure_can` i `module_has_master_key` do `can_send` (wcześniej brak flagi → CONFIG poza discovery był blokowany przy `send_raw` / deep refresh).
- **`app.py`:** skan startowy i `auto_scan` działają gdy `secure_can=false` bez `master_key_hex`; komunikaty „V3 plain CAN — skan bez klucza” zamiast mylących ostrzeżeń.
- **`options.py`:** poprawne parsowanie bool (`"false"` nie jest już traktowane jako true).
- **`bus_manager`:** deep refresh po skanie także w trybie plain CAN; `master_key_required_hint` null gdy `secure_can=false`.
- **Ingress UI:** `_resolve_static_dir()` + weryfikacja `static/` w Dockerfile; banner MASTER_KEY ukryty przy plain CAN.
- **Testy:** `tests/test_can_send_plaintext.py`.

## 2026-07-05 (v0.6.1)

### fix: scan-only add-on UI + MASTER_KEY graceful scan + auto entity import

- **Panel Ingress:** uproszczony do skanu magistrali (status, tabela modułów, komunikat o braku `master_key_hex`).
- **`configurator_engine.scan_modules_sync`:** discovery GET_SUMMARY bez klucza; aktywny odczyt GPIO/relay pomijany bez MASTER_KEY; skan nie pada na wyjątku relay refresh.
- **`/api/discovery`:** pole `discovery_version` (inkrement przy zapisie `modules.json`); integracja nasłuchuje zmian.
- **`can_gateway_v3` (add-on):** auto-import encji z `/api/state` po skanie; tworzenie button/cover z `summary` bez pełnego runtime.
- **`deploy_integration.sh`:** poprawne wywołanie `homeassistant.reload_custom_components` przez Supervisor API.
- **Stabilność:** brak auto-skanu startowego bez MASTER_KEY; `POST /api/scan` opakowany w try/except.
- **Domyślny bitrate:** 125000 (zgodnie z protokołem CAN).
- **Testy:** `tests/test_scan_without_master_key.py`.

## 2026-07-05

### feat: unified HA repo — add-on v0.6.0 + integration v3.4.0

- **Add-on `can_gateway/` v0.6.0:** Supervisor discovery, auto-deploy integracji, `homeassistant_api`, map `homeassistant_config:rw`.
- **Integracja `custom_components/can_gateway_v3/` v3.4.0:** HACS + ręczna instalacja z tego samego repo; bundel w `can_gateway/integration/can_gateway_v3/`.
- **`hacs.json`**, `repository.yaml`, README — jeden URL dla użytkowników HA.
- **Testy:** przeniesione z `can-control-suite` (`tests/test_can_gateway_v3_*`, `test_module_store`, `test_entity_export`).
