Pliki w tym katalogu pochodzą z konfigurator_windows_usb_can/ (ten sam kod protokołu CAN).

Źródło prawdy: konfigurator_windows_usb_can/
- protocol_constants.py
- pinout_data.py
- configurator_engine.py  (headless silnik — skan F5, lazy-load zakładek)

Po zmianach w konfiguratorze Windows skopiuj pliki tutaj przed rebuild dodatku HA.

Uwaga: secure_can/MASTER_KEY (can_secure_transport.py, can_provisioner.py) i profile_schema.py
zostały usunięte — dodatek obsługuje wyłącznie plain CAN V3, bez trybu szyfrowanego provisioningu.
