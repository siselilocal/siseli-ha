"""Entity name translations (add-on option LANGUAGE).

Only the names Home Assistant displays change with the language. unique_id,
entity_id, topics and state values stay English, so switching the language
never breaks an automation or a dashboard: Home Assistant keeps the entity_id
it created on first discovery and just shows the new name.

Keys are the English names as displayed (sensor names after the "Settings - "
style section prefix is trimmed, control labels as declared in mqtt.py). A
name missing here falls back to English; tests/test_truthfulness.py checks
that every current name has a French entry.
"""

from typing import Dict

SUPPORTED_LANGUAGES = ("en", "fr")

FR_NAMES: Dict[str, str] = {
    # Device info
    "Collector ID": "ID du collecteur",
    "Device Type": "Type d'appareil",
    "Output Model": "Modèle de sortie",
    "Mode": "Mode",
    "Active Warnings": "Alertes actives",
    "Warning Flags": "Drapeaux d'alerte",
    "Status Code": "Code d'état",
    "Firmware Info": "Infos firmware",
    "Firmware Version": "Version du firmware",
    "Firmware Build Date": "Date de compilation du firmware",
    "Firmware Build Slot": "Emplacement de compilation du firmware",
    # Battery
    "Battery Voltage": "Tension batterie",
    "Battery Capacity": "Capacité batterie",
    "Battery Charging Current": "Courant de charge batterie",
    "Battery Discharge Current": "Courant de décharge batterie",
    "Calculated Battery Charge Power": "Puissance de charge batterie calculée",
    "Calculated Battery Discharge Power": "Puissance de décharge batterie calculée",
    "Calculated Battery Charge Energy": "Énergie chargée batterie calculée",
    "Calculated Battery Discharge Energy": "Énergie déchargée batterie calculée",
    "Battery Number In Series": "Nombre de batteries en série",
    "Battery Status": "État batterie",
    "Battery Type": "Type de batterie",
    "Configured Battery Bank Capacity": "Capacité du parc batterie configurée",
    "Remaining Capacity": "Capacité restante",
    "Nominal Capacity": "Capacité nominale",
    "Display Mode": "Mode d'affichage",
    "Max Voltage": "Tension max",
    "Max Voltage Cell Position": "Position de la cellule la plus haute",
    "Min Voltage": "Tension min",
    "Min Voltage Cell Position": "Position de la cellule la plus basse",
    "Cell Voltages Decoded": "Tensions des cellules décodées",
    "BMS Cell Delta": "Écart entre cellules BMS",
    **{f"Battery Voltage {n}": f"Tension cellule {n}" for n in range(1, 17)},
    # Grid
    "AC Input Voltage": "Tension d'entrée AC",
    "Mains Frequency": "Fréquence secteur",
    "Mains Current Flow Direction": "Sens du courant secteur",
    "Mains Power": "Puissance secteur",
    "Calculated Mains Power": "Puissance secteur calculée",
    "Calculated Grid Import Power": "Puissance importée du réseau calculée",
    "Calculated Consumed Energy": "Énergie consommée calculée",
    "Calculated Grid Imported Energy": "Énergie importée du réseau calculée",
    "Mains Apparent Power": "Puissance apparente secteur",
    # Load
    "Output Voltage": "Tension de sortie",
    "Output Frequency": "Fréquence de sortie",
    "Output Apparent Power": "Puissance apparente de sortie",
    "Output Active Power": "Puissance active de sortie",
    "Calculated Output Active Power": "Puissance active de sortie calculée",
    "Output Load Percent": "Charge de sortie",
    "Output DC Component": "Composante continue de sortie",
    # PV
    "Generation Power": "Puissance produite",
    "Calculated Generation Energy": "Énergie produite calculée",
    "Calculated Generation Power": "Puissance produite calculée",
    "PV Voltage": "Tension PV",
    "PV Current": "Courant PV",
    "PV Power": "Puissance PV",
    "PV2 Voltage": "Tension PV2",
    "PV2 Current": "Courant PV2",
    "PV2 Power": "Puissance PV2",
    "Daily Electricity Generation": "Production du jour",
    "Monthly Electricity Generation": "Production du mois",
    "Total Electricity Generation": "Production totale",
    "Yearly Electricity Generation": "Production de l'année",
    "PV Temperature": "Température PV",
    "PV2 Temperature": "Température PV2",
    "Solar Charging Switch": "Interrupteur de charge solaire",
    "BUS Voltage": "Tension du bus",
    # Settings / diagnostics
    "Abnormal Fan Speed": "Vitesse de ventilateur anormale",
    "Abnormal Low PV Power": "Puissance PV anormalement basse",
    "Abnormal Temperature Sensor": "Sonde de température anormale",
    "BMS Allow Charging Flag": "BMS : charge autorisée",
    "BMS Allow Discharge Flag": "BMS : décharge autorisée",
    "BMS Automatically Starts SOC After Low": "SOC de redémarrage auto après batterie faible",
    "BMS Average Temperature": "Température moyenne BMS",
    "BMS Charge Current Limit": "Limite de courant de charge BMS",
    "BMS Charge Voltage Limit": "Limite de tension de charge BMS",
    "BMS Charging Current": "Courant de charge BMS",
    "BMS Charging Overcurrent Sign": "BMS : surintensité de charge",
    "BMS Communication Control Function": "BMS : fonction de contrôle de communication",
    "BMS Communication Normal": "Communication BMS normale",
    "BMS Current SOC": "SOC actuel BMS",
    "BMS Discharge Current": "Courant de décharge BMS",
    "BMS Discharge Overcurrent Flag": "BMS : surintensité de décharge",
    "BMS Discharge Voltage Limit": "Limite de tension de décharge BMS",
    "BMS Low Battery Alarm Flag": "BMS : alarme batterie faible",
    "BMS Low Power Fault Flag": "BMS : défaut batterie faible",
    "BMS Low Power SOC": "SOC de verrouillage BMS",
    "BMS Low Temperature Flag": "BMS : température basse",
    "BMS Returns To Battery Mode SOC": "SOC de retour sur batterie",
    "BMS Returns To Mains Mode SOC": "SOC de retour sur secteur",
    "BMS Temperature Too High Flag": "BMS : température trop haute",
    "Battery Equalization Mode": "Mode d'égalisation batterie",
    "Battery Equalization Voltage": "Tension d'égalisation batterie",
    "Battery Not Connected": "Batterie non connectée",
    "Battery Overvoltage Shutdown Voltage": "Tension de coupure surtension batterie",
    "Battery Voltage Higher": "Tension batterie trop haute",
    "Boost Temperature": "Température du boost",
    "Buzzer Function": "Fonction buzzer",
    "Charging Light Status": "Voyant de charge",
    "Charging Main Switch": "Interrupteur principal de charge",
    "Grid Regulation Mode": "Mode réseau (prog 50)",
    "CT Function Switch": "Fonction CT",
    "DC Rectification Temperature": "Température du redresseur DC",
    "Dual Output Mode": "Mode sortie double",
    "EEPROM Data Abnormality": "Données EEPROM anormales",
    "EEPROM Read Write Exception": "Erreur lecture/écriture EEPROM",
    "Equalization Interval": "Intervalle d'égalisation",
    "Equalization Overtime": "Délai max d'égalisation",
    "Equalization Time": "Durée d'égalisation",
    "Fan 1 Speed": "Vitesse ventilateur 1",
    "Fan 1 Status": "État ventilateur 1",
    "Fan 2 Speed": "Vitesse ventilateur 2",
    "Fan 2 Status": "État ventilateur 2",
    "Float Charging Voltage": "Tension de floating",
    "Grid Connected Current": "Courant d'injection réseau",
    "Grid Connection Function": "Fonction de raccordement réseau",
    "Grid Connection Sign": "Indicateur de raccordement réseau",
    "High Frequency Of Mains Power Loss": "Fréquence haute de perte secteur",
    "High Point Of Mains Power Loss Voltage": "Tension haute de perte secteur",
    "Inductor Current": "Courant de l'inductance",
    "Solar Supply Priority": "Priorité de l'alimentation solaire",
    "Input Voltage Too High": "Tension d'entrée trop haute",
    "Inverter Light Status": "Voyant onduleur",
    "Inverter Temperature": "Température onduleur",
    "LCD Back Lighting": "Rétroéclairage LCD",
    "Li Battery Activation Function Switch": "Fonction d'activation batterie lithium",
    "Li Battery Activation Process": "Activation batterie lithium en cours",
    "Low Battery Alarm": "Alarme batterie faible",
    "Low Electric Lock Voltage": "Tension de verrouillage batterie faible",
    "Low Frequency Of Mains Power Loss": "Fréquence basse de perte secteur",
    "Low Point Of Mains Power Loss Voltage": "Tension basse de perte secteur",
    "Machine Over Temperature": "Surchauffe de l'appareil",
    "Main Output Relay Status": "Relais de sortie principale",
    "AC Charging Start Time": "Heure de début de charge secteur",
    "AC Charging Stop Time": "Heure de fin de charge secteur",
    "Solar Feed To Grid": "Injection solaire vers le réseau",
    "Mains Input Range": "Plage d'entrée secteur",
    "Mains Light Status": "Voyant secteur",
    "Max utility charge current": "Courant max de charge secteur",
    "Max. Temperature": "Température max",
    "Maximum Total Charging Current": "Courant de charge max total",
    "MPPT Constant Temperature Mode": "Mode MPPT à température constante",
    "Output Set Frequency": "Fréquence de sortie réglée",
    "Output Set Voltage": "Tension de sortie réglée",
    "Over Temperature Restart Function": "Redémarrage après surchauffe",
    "OverLoaded": "Surcharge",
    "Overload Restart Function": "Redémarrage après surcharge",
    "Overload To Bypass Function": "Bypass en cas de surcharge",
    "Display Returns To Homepage": "Retour auto à l'écran d'accueil",
    "ECO Power Saving": "Mode ECO",
    "Beeps While Primary Source Interrupted": "Bip si la source principale est coupée",
    "Fault Code Record": "Enregistrement des codes de défaut",
    "Parallel Mode Turn Off SOC": "SOC de coupure sortie double",
    "Parallel Mode Turn Off Voltage": "Tension de coupure sortie double",
    "Charger Priority": "Priorité du chargeur",
    "Output Source Priority": "Priorité de la source de sortie",
    "PV Energy Feeding Priority": "Priorité d'utilisation de l'énergie PV",
    "PV Grid Connection Agreement": "Protocole de raccordement PV",
    "Return To Battery Mode Voltage": "Tension de retour sur batterie",
    "Return To Mains Mode Voltage": "Tension de retour sur secteur",
    "Second Delay Time": "Délai sortie secondaire",
    "Second Output Battery Capacity": "Capacité batterie sortie secondaire",
    "Second Output Battery Voltage": "Tension batterie sortie secondaire",
    "Second Output Discharge Time": "Durée de décharge sortie secondaire",
    "Software Version": "Version logicielle",
    "Strong Charging Voltage": "Tension de charge rapide",
    "System Time (Hour Minute)": "Heure de l'onduleur (h:min)",
    "System Time (Year Month Day)": "Date de l'onduleur (AAMMJJ)",
    "Total Number Of Grid Connection": "Nombre total de raccordements réseau",
    "Transformer Temperature": "Température du transformateur",
    "Warning Light Status": "Voyant d'alerte",
    # Raw / helper
    "Mains WdRR Token": "Secteur jeton WdRR",
    "Mains WdRR Value": "Secteur valeur WdRR",
    "Mains WdRR Absolute": "Secteur valeur absolue WdRR",
    "Mains eo8w Code": "Secteur code eo8w",
    "WdRR Token 8 Raw": "WdRR jeton 8 brut",
    "eo8w Flags Raw": "eo8w drapeaux bruts",
    "eo8w Blob Raw": "eo8w bloc brut",
    "Yavb Flags Raw": "Yavb drapeaux bruts",
    "Yavb Code Raw": "Yavb code brut",
    "Yavb Aux Raw": "Yavb auxiliaire brut",
    "Rated Apparent Power": "Puissance apparente nominale",
    "Output Status Bits": "Bits d'état de sortie",
    "Mains Flow Code": "Code du flux secteur",
    "Mains Input Range Code": "Code de plage d'entrée secteur",
    # Legacy aliases
    "Inverter Temperature (legacy)": "Température onduleur (ancien)",
    "Max Charge Current (legacy)": "Courant de charge max (ancien)",
    "Utility Charge Current (candidate)": "Courant de charge secteur (candidat)",
    "Bulk Charging Voltage (legacy)": "Tension de charge rapide (ancien)",
    "Float Charging Voltage (legacy)": "Tension de floating (ancien)",
    "Low Battery Cut-off (legacy)": "Coupure batterie faible (ancien)",
    "Mains Flow State (legacy)": "État du flux secteur (ancien)",
    # Controls (switches, buttons, selects, numbers)
    "Backlight": "Rétroéclairage",
    "Buzzer": "Buzzer",
    "Dual Output": "Sortie double",
    "Overload Automatic Restart": "Redémarrage auto après surcharge",
    "Over Temperature Automatic Restart": "Redémarrage auto après surchauffe",
    "Overload To Bypass": "Bypass en cas de surcharge",
    "Clear Fault Code": "Effacer le code de défaut",
    "Refresh Telemetry": "Actualiser les données",
    "Sync Inverter Clock": "Synchroniser l'horloge de l'onduleur",
    "Grid Working Range": "Plage de fonctionnement réseau",
    "Max Utility Charge Current": "Courant max de charge secteur",
    "BMS Lock Machine SOC": "SOC de verrouillage BMS",
    "Restore Mains Charging SOC": "SOC de reprise de la charge secteur",
    "Restore Battery Discharging SOC": "SOC de reprise de la décharge batterie",
    "Inverter Startup SOC": "SOC de démarrage de l'onduleur",
    "Back To Grid Voltage": "Tension de retour sur secteur",
    "Back To Battery Voltage": "Tension de retour sur batterie",
    "Equalization Voltage": "Tension d'égalisation",
    "Grid-Tie Current": "Courant d'injection réseau",
    "Max Charging Current": "Courant de charge max",
    "Second Output Cut-off SOC": "SOC de coupure 2e sortie",
    "Warning: Confirm Restore Settings": "Attention : confirmer la restauration (écrase les réglages)",
    "Save Inverter Settings": "Sauvegarder les réglages onduleur",
    "Restore Inverter Settings": "Restaurer les réglages onduleur",
    "Settings Backup Status": "État sauvegarde des réglages",
    "Second Output Discharge Time": "Durée de décharge 2e sortie",
    "Second Output Restore Delay": "Délai de rétablissement 2e sortie",
    "Second Output Restore SOC": "SOC de rétablissement 2e sortie",
}

#: Device group titles (the "Siseli Local Inverter 1 <title>" devices).
FR_GROUP_TITLES: Dict[str, str] = {
    "Main": "Principal",
    "Battery": "Batterie",
    "BMS": "BMS",
    "Grid": "Réseau",
    "Load": "Sortie",
    "PV": "PV",
    "Diagnostics": "Diagnostic",
    "Settings Backup": "Sauvegarde/Restauration",
}


#: Programme 50 ("Set country customized regulations"): accepted feed-in grid
#: voltage and frequency per mode, from the manual's revised page 27 (the
#: older page 25 only listed India / Germany / South America). The code is the
#: one the front panel shows. Mode 5 is the manual's default; this firmware
#: shipped at Mode 4.
GRID_MODES = {
    1: ("IND", "195.5-253", "49-51"),
    2: ("GEn", "184-264.5", "47.5-51.5"),
    3: ("SAd", "184-264.5", "57-62"),
    4: ("PAk", "170-264.5", "47.5-53.5"),
    5: ("U2b", "100-280", "47.5-53.5"),
}


def grid_mode_label(mode: int, language: str) -> str:
    """"Mode 1 IND (195.5-253 VAC, 49-51 Hz)" / "Mode 1 IND (195,5-253 VAC : 49-51 Hz)".
    The code is the one the front panel shows (country / region abbreviation)."""
    code, volts, hertz = GRID_MODES[mode]
    if language == "fr":
        return f"Mode {mode} {code} ({volts.replace('.', ',')} VAC : {hertz.replace('.', ',')} Hz)"
    return f"Mode {mode} {code} ({volts} VAC, {hertz} Hz)"


def translate_name(name: str, language: str) -> str:
    """The displayed name in `language`; English (unchanged) if unknown."""
    if language == "fr":
        return FR_NAMES.get(name, name)
    return name


def translate_group_title(title: str, language: str) -> str:
    if language == "fr":
        return FR_GROUP_TITLES.get(title, title)
    return title
