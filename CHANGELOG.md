# Changelog — ha-can-gateway

## 2026-08-06 (add-on v5.0.11–v5.0.14)

### fix: brakujące moduły/encje w HA (10 zamiast 15 urządzeń) + przyciski pulse-relay nigdy nie tworzone

- **BUG #11 — `bus_manager.entities_catalog()` (add-on):** `/api/entities` budował katalog wyłącznie z modułów aktualnie widocznych na CAN (`_export_modules_for_catalog()`), zamiast ze wszystkich perystentnych modułów z dysku. Moduły rzadziej się zgłaszające (12, 101–104, 121) wypadały z katalogu mimo poprawnego zapisu w `/data/modules.json`. Naprawione: użycie `discovery_snapshot()["modules"]` (wszystkie perystentne moduły) do budowy live-values, zamiast tylko live-modułów.
- **BUG #12 — `custom_components/can_gateway_v3/switch.py` (integracja), KRYTYCZNY:** pozostawiony kod debugowy (`desc.get("binding_type", ...)`) zakładał że `desc` to `dict`, podczas gdy to `EntityDescription` (dataclass) — rzucał `AttributeError` przy pierwszym switchu w kolejności przetwarzania. Wyjątek **przerywał całą pętlę** `apply_addon_entities()`, więc wszystkie encje po tym punkcie (moduły 9+, w tym 12, 101–104, 121) nigdy nie trafiały do coordinatora. Błąd był po cichu łapany (`except Exception: _LOGGER.debug(...)`), niewidoczny bez debug loggingu.
- **BUG #13 — `custom_components/can_gateway_v3/button.py`:** platforma `button` nigdy nie rejestrowała `platform_adder` dla dynamicznego katalogu z dodatku — obsługiwała wyłącznie statyczne przyciski Gateway (scan/reboot). Encje `platform="button"` eksportowane przez dodatek dla przekaźników z `pulse_ms > 0` (`..._pulse`) nie miały gdzie trafić. Nie była to regresja — funkcja nigdy nie została dokończona od czasu wprowadzenia integracji.
- **BUG #14 — `custom_components/can_gateway_v3/coordinator.py`:** `platform_adders` (dict z prekonfigurowanymi kluczami platform) nie miał klucza `"button"` — `register_platform_adder("button", ...)` rzucał `KeyError`, co uniemożliwiało setup nowo dodanej obsługi z BUG #13.
- **Weryfikacja on-site:** 166 encji / 16 urządzeń (gateway + 15 modułów, w tym 12, 101, 102, 103, 104, 121), zero tracebacków po naprawie.

### chore: integracja usunięta z HACS

- Integracja `can_gateway_v3` **nie jest już dystrybuowana przez HACS**. Jedyna wspierana ścieżka instalacji: auto-deploy przez dodatek Supervisor (`can_gateway/deploy_integration.sh`) lub ręczna kopia `custom_components/can_gateway_v3/` do `/config/custom_components/`.
- Powód: instalacja przez HACS równolegle z auto-deployem dodatku powodowała, że dwa niezależne mechanizmy nadpisywały ten sam folder `/config/custom_components/can_gateway_v3/` niezsynchronizowanymi wersjami (obserwowane realnie: integracja pokazywała starszą wersję niż dodatek, HACS dodatkowo generował błędy 404 próbując pobrać commit hash jako nazwę brancha).
- README.md i `can_gateway/README.md` zaktualizowane — usunięte instrukcje instalacji przez HACS, zachowana wyłącznie ścieżka ręcznej kopii dla trybu bez Supervisor.

## 2026-07-07 (add-on v0.7.5 + integration v3.5.5)

### fix: cover STOP button — state sync and command validation

- **`can_gateway_v3/cover.py`:** jawne `CoverEntityFeature.STOP`; po REST `set_shutter_command` natychmiastowy refresh `/api/entities` + optymistyczne `direction=0` dla STOP (wcześniej UI zostawało w `opening`/`closing` do 5 s mimo zatrzymania na FW); fallback do `_can_send` gdy REST zwróci błąd.
- **`configurator_engine.set_shutter_command`:** alias `set_position`, odrzucenie `cmd=0` (kierunek ≠ komenda STOP=3).
- **Testy:** payload STOP, engine `stop`, regresja `cmd=0`.

## 2026-07-07 (add-on v0.7.4 + integration v3.5.4)

### fix: cover/shutter control via HA integration (add-on mode)

- **`configurator_engine.set_shutter_command`:** poprawny payload V3 `[V2_CTRL_SHUTTER_CMD, shutter_no, cmd, param]` (wcześniej błędnie `[module_id, shutter_no, …]` — firmware odrzucał ramkę).
- **`can_gateway_v3/addon_setup`:** routing CONTROL_COMMAND przed skrótami CONFIG (reboot); `module_id` z CAN ID, nie z `data[0]`.
- **`can_gateway_v3/cover.py`:** add-on mode woła REST `/api/modules/{id}/shutters/{no}`; `shutter_no` z atrybutów katalogu.
- **`app.py`:** log INFO/WARNING przy `POST …/shutters/{no}`.
- **Testy:** `test_shutter_command_encoding.py` — payload, CAN ID, regresja routingu.

## 2026-07-07 (add-on v0.7.3 + integration v3.5.3)

### fix: MCP23017 / 74HC595 entities missing after strict catalog (v0.7.2)

- **`deep_config`:** synchroniczny `GET_SUMMARY` przed `read_gpio_roles_from_module(summary=…)`; usunięte redundantne pętle MCP/shutter/pulse oparte na `send_config` bez odpowiedzi.
- **`configurator_engine`:** `last_summary_response` z async GET_SUMMARY; fallback `hw_flags` gdy brak summary; pełny parse MCP role dump (relay/button/sensor) → `mcp_pin_roles`; eksport `shift595_q_flags`.
- **`entity_export.py`:** HC595 z `hw_flags` bez warunku `gpio_roles`; MCP z `mcp_pin_roles` / role dump; encje button/binary dla pinów MCP (role 2/3); bez nieużywanych kanałów MCP.
- **Testy:** MCP relay port A pin 0, HC595 relay 17, brak encji dla unused MCP pins.

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
