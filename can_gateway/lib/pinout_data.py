"""Board pinout layouts and strapping notes."""

DEVICE_PINOUTS = {
    "ESP32-WROOM-32E": {
        "left": [36, 39, 34, 35, 32, 33, 25, 26, 27, 14, 12, 13, 23, 22, 1, 3],
        "right": [21, 19, 18, 5, 17, 16, 4, 0, 2, 15, 8, 7, 6, 11, 9, 10],
        "can_tx": 17,
        "can_rx": 18,
    },
    "XIAO ESP32-C6": {
        "left": [
            {"label": "D0", "gpio": 0},
            {"label": "D1", "gpio": 1},
            {"label": "D2", "gpio": 2},
            {"label": "D3", "gpio": 21},
            {"label": "D4", "gpio": 22},
            {"label": "D5", "gpio": 23},
            {"label": "D6", "gpio": 16},
        ],
        "right": [
            {"label": "5V"},
            {"label": "GND"},
            {"label": "3V3"},
            {"label": "D10", "gpio": 18},
            {"label": "D9", "gpio": 20},
            {"label": "D8", "gpio": 19},
            {"label": "D7", "gpio": 17},
        ],
        "can_tx": 20,
        "can_rx": 18,
        "board_image": "xiao_esp32c6.png",
    },
    "Waveshare ESP32-C6 Zero": {
        "left": [
            {"label": "5V"},
            {"label": "GND"},
            {"label": "3V3"},
            {"label": "GP0",  "gpio": 0},
            {"label": "GP1",  "gpio": 1},
            {"label": "GP2",  "gpio": 2},
            {"label": "GP3",  "gpio": 3},
            {"label": "GP4",  "gpio": 4},
            {"label": "GP5",  "gpio": 5},
        ],
        "right": [
            {"label": "TX",   "gpio": 16},
            {"label": "RX",   "gpio": 17},
            {"label": "GP14", "gpio": 14},
            {"label": "GP15", "gpio": 15},
            {"label": "GP18", "gpio": 18},
            {"label": "GP19", "gpio": 19},
            {"label": "GP20", "gpio": 20},
            {"label": "GP21", "gpio": 21},
            {"label": "GP22", "gpio": 22},
        ],
        "can_tx": 0,
        "can_rx": 1,
        "led_pin": 8,
        "led_type": "WS2812",
    },
    "Wemos D1 Mini ESP32": {
        "layout": "four_rows_two_per_side",
        # Lewy zewnetrzny rzad
        "left": [
            {"label": "GND", "functions": ["GND"]},
            {"label": "NC"},
            {"label": "GPIO39", "gpio": 39, "safety_color": "yellow", "functions": ["GPIO39", "SVN", "ADC1", "input-only"]},
            {"label": "GPIO35", "gpio": 35, "safety_color": "yellow", "functions": ["GPIO35", "ADC1", "input-only"]},
            {"label": "GPIO33", "gpio": 33, "safety_color": "green",  "functions": ["GPIO33", "ADC1", "RTC"]},
            {"label": "GPIO34", "gpio": 34, "safety_color": "yellow", "functions": ["GPIO34", "ADC1", "input-only"]},
            {"label": "GPIO14", "gpio": 14, "safety_color": "green",  "functions": ["GPIO14", "MTMS", "ADC2"]},
            {"label": "NC"},
            {"label": "GPIO9",  "gpio": 9,  "reserved": True, "safety_color": "red", "functions": ["GPIO9",  "FLASH:D2"]},
            {"label": "GPIO11", "gpio": 11, "reserved": True, "safety_color": "red", "functions": ["GPIO11", "FLASH:CMD"]},
        ],
        # Lewy wewnetrzny rzad (PCB-pad)
        "left_inner": [
            {"label": "RST", "functions": ["RST"]},
            {"label": "GPIO36", "gpio": 36, "safety_color": "yellow", "functions": ["GPIO36", "SVP", "ADC1", "input-only"]},
            {"label": "GPIO26", "gpio": 26, "safety_color": "green",  "functions": ["GPIO26", "ADC2", "DAC2"]},
            {"label": "GPIO18", "gpio": 18, "safety_color": "green",  "functions": ["GPIO18", "SPI:SCK"]},
            {"label": "GPIO19", "gpio": 19, "safety_color": "green",  "functions": ["GPIO19", "SPI:MISO"]},
            {"label": "GPIO23", "gpio": 23, "safety_color": "green",  "functions": ["GPIO23", "SPI:MOSI"]},
            {"label": "GPIO5",  "gpio": 5,  "safety_color": "yellow", "functions": ["GPIO5",  "SPI:CS",  "strap"]},
            {"label": "3V3", "functions": ["3V3"]},
            {"label": "GPIO13", "gpio": 13, "safety_color": "green",  "functions": ["GPIO13", "MTCK", "ADC2"]},
            {"label": "GPIO10", "gpio": 10, "reserved": True, "safety_color": "red", "functions": ["GPIO10", "FLASH:D3"]},
        ],
        # Prawy wewnetrzny rzad (PCB-pad)
        "right_inner": [
            {"label": "GPIO1",  "gpio": 1,  "reserved": True, "safety_color": "red", "functions": ["GPIO1",  "UART0:TXD"]},
            {"label": "GPIO3",  "gpio": 3,  "reserved": True, "safety_color": "red", "functions": ["GPIO3",  "UART0:RXD"]},
            {"label": "GPIO22", "gpio": 22, "safety_color": "green",  "functions": ["GPIO22", "I2C:SCL"]},
            {"label": "GPIO21", "gpio": 21, "safety_color": "green",  "functions": ["GPIO21", "I2C:SDA"]},
            {"label": "GPIO17", "gpio": 17, "reserved": True, "safety_color": "red", "functions": ["GPIO17", "CAN:RX"]},
            {"label": "GPIO16", "gpio": 16, "reserved": True, "safety_color": "red", "functions": ["GPIO16", "CAN:TX"]},
            {"label": "GND", "functions": ["GND"]},
            {"label": "5V",  "functions": ["5V"]},
            {"label": "GPIO15", "gpio": 15, "safety_color": "yellow", "functions": ["GPIO15", "MTDO", "strap"]},
            {"label": "GPIO7",  "gpio": 7,  "reserved": True, "safety_color": "red", "functions": ["GPIO7",  "FLASH:D0"]},
        ],
        # Prawy zewnetrzny rzad
        "right": [
            {"label": "GND", "functions": ["GND"]},
            {"label": "GPIO27", "gpio": 27, "safety_color": "green",  "functions": ["GPIO27", "ADC2", "RTC"]},
            {"label": "GPIO25", "gpio": 25, "safety_color": "green",  "functions": ["GPIO25", "ADC2", "DAC1"]},
            {"label": "GPIO32", "gpio": 32, "safety_color": "green",  "functions": ["GPIO32", "ADC1", "RTC"]},
            {"label": "GPIO12", "gpio": 12, "safety_color": "yellow", "functions": ["GPIO12", "MTDI", "strap"]},
            {"label": "GPIO4",  "gpio": 4,  "safety_color": "yellow", "functions": ["GPIO4",  "ADC2", "strap"]},
            {"label": "GPIO0",  "gpio": 0,  "safety_color": "yellow", "functions": ["GPIO0",  "BOOT", "strap"]},
            {"label": "GPIO2",  "gpio": 2,  "safety_color": "yellow", "functions": ["GPIO2",  "LED",  "strap"]},
            {"label": "GPIO8",  "gpio": 8,  "reserved": True, "safety_color": "red", "functions": ["GPIO8",  "FLASH:D1"]},
            {"label": "GPIO6",  "gpio": 6,  "reserved": True, "safety_color": "red", "functions": ["GPIO6",  "FLASH:CLK"]},
        ],
        "can_tx": 16,
        "can_rx": 17,
        "led_pin": 2,
        "uart_reserved": [1, 3],
        "flash_reserved": [6, 7, 8, 9, 10, 11],
    },
}

# Legacy display name kept for older saved profiles/tests.
DEVICE_PINOUTS["ESP32 DevKit V1"] = DEVICE_PINOUTS["ESP32-WROOM-32E"]

STRAPPING_PIN_NOTES = {
    "ESP32-WROOM-32E": {
        0: "GPIO0: pin boot. Nie wymuszaj stanu niskiego podczas resetu (inaczej tryb programowania).",
        2: "GPIO2: pin strapping. Utrzymuj neutralny stan podczas resetu i nie obciazaj go twardo.",
        4: "GPIO4: pin strapping. Nie wymuszaj stalego poziomu w trakcie resetu.",
        5: "GPIO5: pin strapping. Nie wymuszaj stalego poziomu w trakcie resetu.",
        12: "GPIO12: pin strapping VDD_SDIO. Nie podawaj twardego stanu przy starcie.",
        15: "GPIO15: pin strapping. Uzywaj przez driver i unikaj wymuszania poziomu przy boot.",
    },
    "Wemos D1 Mini ESP32": {
        0:  "GPIO0 (D9): pin boot. Nie sciagaj do GND przy starcie.",
        2:  "GPIO2 (D11): pin strapping oraz LED. Nie wymuszaj stalego poziomu podczas resetu.",
        4:  "GPIO4 (D10): pin strapping. Uzywaj przez driver i unikaj twardego wymuszania poziomu.",
        5:  "GPIO5 (D8): pin strapping. Uzywaj przez driver i unikaj twardego wymuszania poziomu.",
        12: "GPIO12 (D14): KRYTYCZNY pin strapping VDD_SDIO. Jesli podciagniety do VCC przy starcie, ESP32 przechodzi na 1.8V dla flash i nie startuje. Uzywaj wylacznie przez transoptor i nigdy nie podciagaj do 3.3V!",
        15: "GPIO15 (D12): pin strapping. Nie wymuszaj stalego poziomu przy starcie.",
        36: "GPIO36 (D15): pin TYLKO DO ODCZYTU (input-only, SVP). Brak wewnetrznego pull-up/pull-down. Nie mozna go uzyc jako wyjscie (relay). Nadaje sie jako przycisk/sensor binarny.",
    },
    "Waveshare ESP32-C6 Zero": {
        15: "GPIO15: pin startowy (strap). Nie wymuszaj stalego poziomu podczas resetu.",
    },
    "XIAO ESP32-C6": {},
}
