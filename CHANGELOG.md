# Changelog — ha-can-gateway

## 2026-08-06 (add-on v5.0.16)

### refactor: usunięcie gs_usb i secure_can/MASTER_KEY, grupowanie opcji dodatku

Motywacja: dodatek miał 16 opcji konfiguracyjnych, sporo z nich nieużywanych w rzeczywistym wdrożeniu (WeAct USB2CAN po SLCAN, plain CAN V3 bez szyfrowania). Firmware V3 nie implementuje już kanału Secure CAN; kod obsługujący `secure_can`/MASTER_KEY był martwy w praktyce, a `gs_usb` to alternatywny sterownik USB-CAN nieużywany przy obecnym sprzęcie.

**Usunięte całkowicie:**
- **gs_usb**: monkey-patch `_apply_cannectivity_gs_usb_out_ep_patch()`, gałąź `if iface == CAN_INTERFACE_GS_USB` w `bus_manager._open_bus()`, opcje `gsusb_channel`/`can_interface: gs_usb`, zależność `gs-usb` z Dockerfile. `can_interface` zostaje jako pole (obecnie zawsze `slcan`), zachowane dla ewentualnej przyszłej rozszerzalności.
- **secure_can / MASTER_KEY**: cały plik `lib/can_secure_transport.py` (AES-ECB + HMAC-SHA256, `SecureCanTransport`), `can_service/provision_service.py` + 3 endpointy REST (`/api/modules/{id}/master-key` GET/POST, `/api/modules/{id}/provision-state`), `lib/can_provisioner.py` (osierocony, nigdzie nieimportowany), `lib/profile_schema.py` (osierocony). W `configurator_engine.py` usunięte: pola `_secure_can`/`_master_key`/`has_master_key`, metody `_sync_module_master_key_state`, `_probe_module_key_match`, `_module_has_master_key(_for_tx)`, `_module_key_mismatch_detected`, `_is_module_comm_blocked`, `_send_request_use_secure_tlv`, `_secure_tlv_node_key(_send_and_wait)`; `_secure_bus_send` uproszczone do bezpośredniego `send_can_frame` (nazwa zachowana dla kompatybilności wywołań). `can_send.prepare_outgoing_frames` przycięte z ~30 linii do 3 (zawsze plaintext). Usunięte pola `AddonOptions.secure_can`/`master_key_hex`/`master_key_bytes`.
- Zduplikowana logika w `bus_manager.discovery_scan()` (`if secure_can... elif not secure_can...` z identycznym ciałem) scalona w jedną bezwarunkową pętlę.
- Panel web: baner `key-banner` (JS `updateKeyBanner`, HTML element, CSS reguła) i wiersz „Secure CAN” w tabelce statusu — martwe UI bez odpowiednika w API.

**Grupowanie pozostałych opcji** (opcja użytkownika: „grupowanie tylko dla pozostałych opcji"): `config.yaml` `options`/`schema` przeorganizowane z płaskiej listy 16 pól na 3 zagnieżdżone sekcje — `connectivity` (can_interface, can_port, can_bitrate, tty_baudrate), `auto_scan` (enabled, interval_s), `mqtt` (enabled, host, port, username, password, topic_prefix, interval_s). Supervisor renderuje zagnieżdżone obiekty jako zwijane sekcje w UI konfiguracji dodatku. `options.py` (`load_options()`) i `run.sh` (`bashio::config 'connectivity.can_interface'` itd.) zaktualizowane pod nową strukturę.

**Breaking change:** struktura `options.json` zmienia się z płaskiej na zagnieżdżoną — istniejąca konfiguracja dodatku wymaga ponownego ustawienia w UI Supervisor po aktualizacji (zaakceptowane świadomie — jedyny użytkownik tego wdrożenia).

**Testy:** `test_can_send_plaintext.py` przepisany pod nową sygnaturę `prepare_outgoing_frames(module_id, can_id, data)`; `test_scan_without_master_key.py` usunięty (testował warunkowe `active_relay_read` na podstawie `master_key`, które już nie istnieje — `refresh_all_module_relay_states` woła się teraz zawsze z `active=True`); `test_shutter_command_encoding.py` zaktualizowany pod nowy konstruktor `ConfiguratorEngine(io)` bez `secure_can`/`master_key`. 53/53 testów przechodzi.

## 2026-08-06 (add-on v5.0.15)

### refactor: usunięcie trybu "direct serial", integracja wyłącznie w trybie add-on

- **Usunięty tryb direct serial** z `custom_components/can_gateway_v3/`: integracja obsługiwała dawniej dwa tryby połączenia — `addon` (REST API dodatku) i `serial` (integracja sama otwierała port USB/CAN, niezależnie od dodatku). Utrzymywanie dwóch niezależnych implementacji odczytu/dekodowania magistrali CAN (dodatek + integracja) było źródłem duplikacji i ryzyka rozjazdu logiki. Integracja działa teraz **wyłącznie** jako cienki klient REST API dodatku.
- **Usunięte pliki/kod:** `can_io.py` (implementacja `SlcanSerialBridge`, cała obsługa protokołu SLCAN po porcie szeregowym) — usunięty całkowicie; współdzielone aliasy typów (`CanFrameSender`, `RawPayloadCallback`) przeniesione do nowego `types.py`. W `__init__.py` usunięta cała gałąź fallback `async_setup_entry`/`async_unload_entry` dla trybu serial oraz osiem pomocniczych funkcji skanu magistrali używanych wyłącznie w tej gałęzi. W `config_flow.py` usunięte kroki `async_step_serial`, `async_step_scan_progress`, `async_step_finish`, `_run_discovery_scan`, `_detect_default_serial_port` i pokrewne — krok `user` przechodzi teraz od razu do `async_step_addon` (jedyny dostępny tryb). W `const.py` usunięte stałe specyficzne dla serial (`CONNECTION_MODE_SERIAL`, `CONF_SERIAL_PORT`, `CONF_SERIAL_BAUDRATE`, `CONF_CAN_BITRATE`, `DEFAULT_SERIAL_PORT`, `DEFAULT_SERIAL_BAUDRATE`, `DEFAULT_CAN_BITRATE`). Usunięta zależność `pyserial` z `manifest.json`.
- **`sensor.py`:** encja `Gateway Status` raportowała atrybuty `serial_port`/`serial_baudrate`/`can_bitrate` nawet w trybie add-on, gdzie te pola nigdy nie były ustawiane w `entry.data` — zawsze pokazywały fałszywe wartości domyślne. Zastąpione pojedynczym atrybutem `addon_api_url`.
- **`if entry.data.get(connection_mode) != "addon": return False`** — jeśli ktoś ma jeszcze stary config entry z `connection_mode=serial` z poprzedniej wersji, `async_setup_entry` teraz jawnie odmawia setupu z komunikatem błędu zamiast próbować (nieistniejącej już) ścieżki serial.
- **Testy:** 56/56 przechodzi bez zmian (żaden test nie odwoływał się do trybu serial).

### chore: nazwa integracji bez numeru protokołu w display name

- Wyświetlana nazwa integracji zmieniona z „CAN Gateway v3" na „CAN Gateway" (manifest `name`, tytuły kroków config_flow, urządzenie bramki w device_info, logi startowe) — `v3` w folderze/domenie (`can_gateway_v3`) było mylone z numerem wersji softu, podczas gdy w rzeczywistości odnosi się do generacji protokołu CAN. Numer wersji softu (`5.0.15`) jest już widoczny osobno w polu „Wersja" w UI integracji. **Techniczny `domain` w manifest.json (`can_gateway_v3`) pozostaje bez zmian** — zmiana domeny wymagałaby usunięcia i ponownego dodania integracji w HA (utrata wszystkich encji/urządzeń), więc świadomie tego uniknięto.

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
