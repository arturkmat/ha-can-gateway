# Changelog — ha-can-gateway

## 2026-08-06 (add-on v5.0.29)

### fix: binarne wejścia MCP23017 miały odwróconą logikę (True/False zamienione)

- **Objaw:** po V5.0.28 encje `binary_sensor.can_m121_mcp6_pin*` w końcu pokazywały realne wartości zamiast "unknown" — ale wszystkie odwrotnie: piny w spoczynku (nienaciśnięte/nieaktywne) pokazywały się jako `on`, a aktywne jako `off`.
- **Przyczyna:** `_mcp_pin_value()` w `entity_export.py` zwracał surowy bit z rejestru GPA/GPB (`bool(register & (1 << bit))`) bez uwzględnienia okablowania active-low z podciąganiem (pull-up) — stan spoczynkowy trzyma linię w stanie wysokim (bit=1), zadziałanie ściąga ją do zera (bit=0). Zwykłe GPIO (nie-MCP) nie miało tego problemu, bo firmware wysyła tam już przetworzoną wartość `logical` — dla rejestrów MCP23017 dostajemy surowy odczyt sprzętowy i inwersję trzeba zrobić samodzielnie.
- **Fix:** `_mcp_pin_value()` zwraca teraz zanegowany bit (`not bool(...)`) — bit=1 (spoczynek) → `False`/off, bit=0 (aktywne) → `True`/on. Dotyczy obu ról korzystających z tej funkcji: przycisków MCP (`role_code=2`, platforma `sensor`) i zwykłych wejść binarnych MCP (`role_code=3`, platforma `binary_sensor`).
- **Testy:** nowy `test_build_entities_mcp_binary_pin_value_is_active_low` w `tests/test_entity_export.py` — weryfikuje inwersję dla obu bajtów rejestru (GPA i GPB). 62/62 przechodzi.



## 2026-08-06 (add-on + integration v5.0.28)

### fix: docelowa naprawa "unknown" na binary_sensor MCP23017 modułu 121 — dwa równoległe mechanizmy skanu MCP walczyły o tę samą magistralę

- **Objaw:** mimo V5.0.25-27 (poprawny argument chip_idx, poprawne indeksy gpa/gpb, poprawne wykrywanie chipa przez found_mask, scalanie do `module_detail()`) logi pokazywały poprawny odczyt (`status=0, gpa=255, gpb=191`), a `/api/modules/121` i tak zwracał `mcp_input_state: None`.
- **Prawdziwa przyczyna:** w kodzie istniały **dwa całkowicie niezależne mechanizmy** odczytu MCP23017 dla tego samego modułu, wywoływane jeden po drugim w tej samej rundzie deep-refresh:
  1. `configurator_engine.py`: `read_gpio_roles_from_module()` — poprawnie skanuje `found_mask` i pobiera `ROLE_DUMP` per chip, zapisując do `ModuleContext` (`ctx.mcp_relay_pins`/`ctx.mcp_pin_roles`) — **to jest jedyne miejsce, które faktycznie zasila `module_detail()`/`/api/entities`** (przez `export_module_dict()`).
  2. `bus_manager.py`: `ensure_relay_metadata()` (dodane w V5.0.25/26) — osobny skan tego samego modułu, zapisujący do `self._modules[mid].runtime` — **strukturę, której nic więcej w tym wdrożeniu nie zapełnia** (`/api/status` pokazywał `module_count: 0` — cały mechanizm śledzenia modułów w `bus_manager.py` jest martwy, żywe dane trzyma wyłącznie silnik `configurator_engine`).
  - Efekt: odczyt z (2) był poprawny na poziomie logów, ale zapisywał się donikąd (`rec is not None` nigdy nie było prawdą), a jednocześnie (2) generował dodatkowy, zbędny ruch na magistrali CAN wobec tego samego modułu co (1) w tej samej rundzie — źródło przejściowych `None`/timeoutów widocznych w logach dla innych chipów.
- **Fix:** `mcp_input_state` przeniesione do `ModuleContext` (`configurator_engine.py`) i wypełniane bezpośrednio w `read_gpio_roles_from_module()`, w tej samej pętli co już działający `ROLE_DUMP` (ten sam `found_mask`, ten sam chip) — zero dodatkowego ruchu na magistrali. `export_module_dict()` eksportuje je do `runtime["mcp_input_state"]` obok `mcp_relay_pins`/`mcp_pin_roles`. Cały zbędny, niedziałający mechanizm w `bus_manager.py` (`ensure_relay_metadata()`, wywołanie go z pętli deep-refresh, tymczasowy merge w `module_detail()`) usunięty.
- **Testy:** `tests/test_mcp_input_state.py` przepisany pod nową lokalizację — 4 testy na `ConfiguratorEngine.read_gpio_roles_from_module()`/`export_module_dict()` (argument chip_idx, zapis stanu, brak nadpisania dobrych danych błędnym statusem, obecność w eksportowanym `runtime`). 61/61 przechodzi.
- **Wersje:** dodatek i integracja zbumpowane razem (5.0.28) — integracja realnie się nie zmieniła od 5.0.24 (poprzednie poprawki 5.0.25-27 były wyłącznie po stronie dodatku), ale numer podniesiony dla spójności z panelem "Ustawienia -> Urządzenia i usługi -> CAN Gateway", żeby nie sugerował że coś nie dotarło. **Uwaga:** zmiana wersji integracji wymaga pełnego restartu Home Assistant Core, żeby była widoczna w UI (sam restart dodatku tego nie robi — kopiuje pliki do `/config/custom_components`, ale nie przeładowuje już załadowanego modułu Pythona).



## 2026-08-06 (add-on v5.0.27)

### fix: poprawnie odczytany stan wejść MCP23017 nigdy nie docierał do /api/entities dla modułów "żywych" w silniku

- **Objaw:** po V5.0.25+V5.0.26 logi pokazywały już PRAWIDŁOWY odczyt dla modułu 121: `MCP input response module=121 chip=6 raw=[121, 68, 0, 255, 191, 0, 0, 0]` (status=0, realne dane), a mimo to encje `binary_sensor.can_m121_mcp6_pin*` w HA dalej pokazywały `unknown`.
- **Przyczyna:** `module_detail()` w `bus_manager.py` ma dwie ścieżki: dla modułu, który silnik `configurator_engine` uważa za "żywy" (jest w `discovered_modules` albo ma własny `_contexts[mid]` — a moduł 121 zawsze taki jest, bo regularnie się odzywa), zwraca `engine.export_module_dict(mid)` **w całości z pominięciem** `self._modules[mid].runtime` — czyli z pominięciem miejsca, w którym `ensure_relay_metadata()` zapisuje odczytany `mcp_input_state`. `ModuleContext` w `configurator_engine.py` ma osobny, równoległy mechanizm dla MCP23017, ale śledzi tylko **role pinów** (`mcp_pin_roles`/`mcp_relay_pins`), nie ma w ogóle pojęcia o **bieżącej wartości rejestrów** (gpa/gpb) — więc nawet poprawnie odczytany stan nie miał gdzie wylądować w odpowiedzi API.
- **Fix:** `module_detail()` łączy teraz (`_with_mcp_input_state()`) wynik `engine.export_module_dict()` z `mcp_input_state` przechowywanym lokalnie w `self._modules[mid].runtime` — dla obu gałęzi uznających moduł za "żywy" w silniku. Ścieżka fallback (moduł nieznany silnikowi) już wcześniej działała poprawnie, bo korzysta bezpośrednio z `ModuleRecord.to_dict(include_runtime=True)`.
- **Testy:** nowy `test_module_detail_merges_mcp_input_state_for_engine_known_module` — symuluje silnik zwracający `runtime` bez `mcp_input_state` i weryfikuje że po scaleniu dane z `ensure_relay_metadata()` są widoczne, z zachowaniem pozostałych pól z silnika. 62/62 przechodzi.
- **Ubocznie:** to wyjaśnia też dlaczego problem wyglądał na "naprawiony w logach, ale dalej zepsuty w HA" — odczyt z magistrali był od V5.0.26 poprawny, ale ginął jeden poziom wyżej, między odczytem a zbudowaniem katalogu encji.



## 2026-08-06 (add-on v5.0.26)

### fix: skan MCP23017 permanentnie gubił chip modułu po każdym restarcie dodatku (regresja z "Limit MCP role scan to reported chips")

- **Objaw:** zaraz po wdrożeniu V5.0.25 (fix odczytu stanu wejść MCP) i restarcie dodatku, moduł 121 nadal dostawał `chip=0` zamiast `chip=6` przy odpytywaniu `COMMAND_GET_MCP23017_INPUT_STATE`/`ROLE_DUMP` — mimo że przed restartem `mcp_relay_pins` poprawnie znało chip 6.
- **Przyczyna:** wcześniejsza "optymalizacja" (`chips = known_chips or [0]`) ograniczała skan ról MCP tylko do chipów już wcześniej odkrytych w `runtime.mcp_relay_pins` — ale to czysto pamięciowy cache, zerowany przy każdym starcie procesu (`_load_persisted_modules()` hydratuje tylko oddzielny słownik do snapshotów API, NIE `self._modules[mid].runtime`). Po restarcie `known_chips` zawsze zaczynało puste, więc kod zawsze próbował chip 0 — dla modułu 121 (chip 6, I2C 0x26) to się NIGDY nie powodzi, więc `known_chips` nigdy się nie zapełnia ponownie i moduł jest trwale "ślepy" na swój MCP23017 aż do kolejnej zmiany kodu.
- **Fix:** `COMMAND_SCAN_MCP23017` (który realnie skanuje I2C 0x20..0x27 i zwraca `found_mask` — bitmaskę odpowiadających adresów) jest teraz faktycznie odczytywany zamiast być wysyłanym "w próżnię". Lista chipów do odpytania budowana jest z `found_mask` przy każdym wywołaniu `ensure_relay_metadata()`; pamięciowy cache `known_chips` służy tylko jako fallback gdy skan nic nie zwrócił, a pełne przejście 0..7 jako ostateczność gdy nic nie jest jeszcze znane.
- **Testy:** nowy `test_ensure_relay_metadata_uses_scan_found_mask_when_nothing_known_yet` — symuluje stan "świeży proces, `mcp_relay_pins` puste" i weryfikuje że mimo to chip 6 zostaje poprawnie odkryty z `found_mask` zamiast trwale utknąć na chip 0. 61/61 przechodzi.



## 2026-08-06 (add-on v5.0.25)

### fix: binarne sensory MCP23017 modułu 121 zawsze "unknown" (błędne odpytywanie stanu wejść)

- **Objaw:** encje `binary_sensor.can_m121_mcp6_pin*` (wejścia na ekspanderze I2C MCP23017 modułu 121, `mcp=0x26`) miały stan `unknown` bez końca — mimo że role pinów (button/binary) były poprawnie wykryte i encje istniały.
- **Przyczyna:** `ensure_relay_metadata()` w `bus_manager.py` wysyłał `COMMAND_GET_MCP23017_INPUT_STATE` (68) **bez argumentu chip_idx**, w odróżnieniu od `COMMAND_GET_MCP23017_ROLE_DUMP` (70), który poprawnie przekazuje `[chip]`. Firmware bez znajomości chipa odpowiadał uniwersalnym błędem `status=2` dla **każdego** modułu (z ekspanderem i bez) — potwierdzone w logach (`MCP input response module=121 raw=[121, 68, 2, 0, 0, 0, 0, 0]`, identycznie dla wszystkich 15 modułów). Dodatkowo odczyt `gpa`/`gpb` z odpowiedzi używał złych indeksów (`response[-2]/response[-1]` zamiast `response[3]/response[4]`).
- **Fix:** `COMMAND_GET_MCP23017_INPUT_STATE` wysyłane teraz per znany chip z `args=[chip]`, analogicznie do ROLE_DUMP; wynik zapisywany pod kluczem `str(chip)` w `mcp_input_state` (zamiast na sztywno pod `"0"`); poprawione indeksy `gpa=response[3]`, `gpb=response[4]`; błędny status (`!= 0`) dla danego chipa jest pomijany, nie nadpisuje istniejących dobrych danych zerami.
- **Testy:** nowy `tests/test_mcp_input_state.py` (3 testy: argument chip_idx faktycznie wysyłany, poprawne zapisanie gpa/gpb pod właściwym kluczem chipa, błąd statusu nie kasuje wcześniej odczytanego stanu). 60/60 przechodzi.

## 2026-08-06 (5.0.19 → 5.0.24, skrót)

### fix: seria drobnych poprawek stanów binarnych, przycisków i reconnectów SLCAN

- **Binary sensor (integracja):** `is_on`/`_coerce_bool`/`_coerce_binary_state` — normalizacja wartości z JSON (bool/int/string) zamiast gołego `bool(value)`, które błędnie rzutowało np. `"0"`/`"false"` na `True`.
- **Przycisk jako binary_sensor (`m{id}_btn{n}_pressed`):** naprawiony brak resetu do `False` — poprzednio stan zostawał `True` na stałe po pierwszym naciśnięciu (nic go nie cofało). Teraz auto-reset po 0.75s (`_button_reset_handles` + `hass.loop.call_later`), z anulowaniem poprzedniego timera przy kolejnym szybkim kliknięciu.
- **Wirtualne mapowanie pulsu:** moduł 103 przekaźnik 23 → puls na `m201_gpio120_binary` (`coordinator.pulse_binary_sensor()`), dedykowane pod konkretne okablowanie.
- **`entity_export.py`:** te same reguły koercji zastosowane do budowy encji GPIO z katalogu (`valid`/`logical`).
- **`addon_sync.py`:** normalizacja `value` dla platformy `binary_sensor` przy synchronizacji z żywego API dodatku (`apply_addon_entities`/`apply_addon_entity_values`).
- **`bus_manager.py`:** serializacja reconnectów SLCAN podczas skanowania (`_reconnect_lock`, unikanie równoległego `_try_reopen()` z pętlą reconnect); ograniczenie skanu ról MCP do zgłoszonych przez firmware chipów zamiast prób 8 adresów za każdym razem; priorytetyzacja odświeżania persystowanych modułów.
- **Watchdog magistrali CAN** (`_watchdog_loop`/`_force_bus_reset`/`_mark_bus_activity`) — wykrywa zawieszony `_io_lock` (np. `recv()` ignorujące timeout na zdegradowanym łączu USB-serial) i wymusza reset portu bez czekania na zablokowany lock; naprawia zgłoszenie "kilkukrotne naciśnięcie przycisku zawiesza komunikację, pomaga restart dodatku".
- **Testy:** `tests/test_bus_watchdog.py` (3 testy).


## 2026-08-06 (add-on v5.0.18)

### fix: pozycja rolet/stan przekaźników zamrożone od ostatniego skanu (regresja z BUG #11)

- **Objaw:** procent otwarcia rolety nie aktualizował się na żywo podczas jazdy (brak "animacji") — pozostawał zamrożony na wartości z ostatniego jawnego skanu magistrali.
- **Przyczyna:** BUG #11 (V5.0.11, dzisiaj wcześniej) naprawiał problem "10 modułów zamiast 15" w `/api/entities`, zmieniając źródło listy modułów z żywego silnika (`_export_modules_for_catalog()`, filtrowało tylko moduły odpowiadające w danym momencie) na perystentny snapshot z dysku (`store["modules"]`, kompletny ale zamrożony w momencie ostatniego `persist_discovery_state()`). To naprawiło kompletność, ale jako efekt uboczny zamroziło **runtime state** (pozycja rolety, stan przekaźnika) — `/api/entities` budowało katalog encji z `mod["runtime"]` wziętego wprost z dysku, ignorując bieżący stan modułu w pamięci silnika (aktualizowany bez przerwy przez wątek RX CAN).
- **Fix:** `entities_catalog()` w `bus_manager.py` używa teraz perystentnej listy modułów **tylko jako listy identyfikatorów** (dla kompletności — wszystkie 15, nie tylko aktualnie odpowiadające), ale dla każdego modułu resolvuje jego `runtime` przez `module_detail()`, który w pierwszej kolejności sięga po żywy kontekst silnika (`engine._contexts`/`discovered_modules`, aktualizowany na bieżąco) i dopiero w braku takiego kontekstu spada na dane z dysku. `module_detail()` nie wykonuje żadnego blokującego I/O na magistrali CAN (tylko odczyt z pamięci), więc nie ma ryzyka powrotu do timeoutu z BUG #10.
- **Testy:** nowy test regresyjny `test_entities_catalog_uses_live_module_detail_not_frozen_disk_snapshot` weryfikuje że katalog encji odzwierciedla żywą pozycję z `module_detail()`, nie zamrożoną wartość z dysku, dla modułów mających aktywny kontekst silnika, z poprawnym fallbackiem na dysk dla modułów bez żywego kontekstu. 54/54 testów przechodzi.

## 2026-08-06 (integration v5.0.16)

### fix: przekaźniki "zombie" po wielokrotnych szybkich skanach (unique_id race condition)

- **Objaw:** po kilku skanach magistrali w krótkim czasie, część encji `switch` przestawała reagować na OFF (slider się przesuwał, ale przekaźnik fizycznie zostawał włączony) — działało dopiero szybkie ON→OFF. W logach HA widoczne: `Platform can_gateway_v3 does not generate unique IDs. ID m101_hc595_relay24 already exists — ignoring`.
- **Przyczyna:** `_poll_discovery()` w `addon_setup.py` woła `_reload_platforms()` (pełny `async_unload_platforms` + `async_forward_entry_setups`) za **każdą** zmianą `discovery_version` — a to rośnie przy każdym skanie. Przy kilku skanach pod rząd kolejne reloady odpalały się bez odstępu; HA nie zawsze zdążał w pełni wyczyścić poprzednią rundę (rejestr encji w pamięci, nie na dysku) zanim zaczynała się kolejna, co powodowało przejściowy konflikt `unique_id` — nowa (poprawna) instancja encji była **ignorowana** przez HA, a stara "zombie" instancja zostawała widoczna w UI z nieaktualnym stanem/referencją do `can_send`.
- **Fix:** dodany debounce w `_reload_platforms()` — wymuszony minimalny odstęp 4 s między kolejnymi zakończeniami reload (`asyncio.sleep` na resztę okna, nie pomijanie aktualizacji). Nie zmienia semantyki, tylko daje Home Assistantowi czas na pełne posprzątanie poprzedniej rundy.
- **Obejście natychmiastowe:** pełny restart Home Assistant Core czyści "zombie" encje od razu (zastosowane podczas diagnozy).
- **Testy:** 53/53 przechodzi (brak dedykowanego testu dla debounce — wymagałby symulacji HA config_entries API).

## 2026-08-06 (add-on v5.0.17)

### refactor: usunięcie mostu MQTT

- Usunięty `can_service/mqtt_bridge.py` (cały plik) oraz jego inicjalizacja w `app.run_server()` (`mqtt.start()`/`mqtt.stop()`). Pole `mqtt_enabled` usunięte z `BusManager.status()`.
- Usunięte pola `AddonOptions.mqtt_*` (7 pól) i sekcja `mqtt` z `config.yaml` (options/schema) — zostają tylko `connectivity` i `auto_scan`.
- Usunięta zależność `paho-mqtt` z Dockerfile. Przy okazji usunięta też `cryptography` — zapomniana martwa zależność z V5.0.16 (była tylko dla usuniętego wtedy `can_secure_transport.py`/`can_provisioner.py`, nic jej już nie importuje).
- **Testy:** 53/53 przechodzi bez zmian (MQTT nie miał testów jednostkowych).

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
