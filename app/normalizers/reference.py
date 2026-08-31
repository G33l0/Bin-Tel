"""Static reference data: ISO 3166-1 countries, ISO 4217 currencies, subdivisions.

Embedded rather than downloaded so that normalization works identically in the
desktop application, the importers and the test-suite — including offline.
"""

from __future__ import annotations

from typing import Final, NamedTuple


class CountryRecord(NamedTuple):
    iso2: str
    iso3: str
    numeric: str
    name: str
    currency: str
    region: str


# iso2|iso3|numeric|name|currency|region
_COUNTRY_DATA: Final[str] = """
AD|AND|020|Andorra|EUR|Europe
AE|ARE|784|United Arab Emirates|AED|Asia
AF|AFG|004|Afghanistan|AFN|Asia
AG|ATG|028|Antigua and Barbuda|XCD|Americas
AI|AIA|660|Anguilla|XCD|Americas
AL|ALB|008|Albania|ALL|Europe
AM|ARM|051|Armenia|AMD|Asia
AO|AGO|024|Angola|AOA|Africa
AQ|ATA|010|Antarctica||Antarctica
AR|ARG|032|Argentina|ARS|Americas
AS|ASM|016|American Samoa|USD|Oceania
AT|AUT|040|Austria|EUR|Europe
AU|AUS|036|Australia|AUD|Oceania
AW|ABW|533|Aruba|AWG|Americas
AX|ALA|248|Åland Islands|EUR|Europe
AZ|AZE|031|Azerbaijan|AZN|Asia
BA|BIH|070|Bosnia and Herzegovina|BAM|Europe
BB|BRB|052|Barbados|BBD|Americas
BD|BGD|050|Bangladesh|BDT|Asia
BE|BEL|056|Belgium|EUR|Europe
BF|BFA|854|Burkina Faso|XOF|Africa
BG|BGR|100|Bulgaria|BGN|Europe
BH|BHR|048|Bahrain|BHD|Asia
BI|BDI|108|Burundi|BIF|Africa
BJ|BEN|204|Benin|XOF|Africa
BL|BLM|652|Saint Barthélemy|EUR|Americas
BM|BMU|060|Bermuda|BMD|Americas
BN|BRN|096|Brunei Darussalam|BND|Asia
BO|BOL|068|Bolivia|BOB|Americas
BQ|BES|535|Bonaire, Sint Eustatius and Saba|USD|Americas
BR|BRA|076|Brazil|BRL|Americas
BS|BHS|044|Bahamas|BSD|Americas
BT|BTN|064|Bhutan|BTN|Asia
BV|BVT|074|Bouvet Island|NOK|Antarctica
BW|BWA|072|Botswana|BWP|Africa
BY|BLR|112|Belarus|BYN|Europe
BZ|BLZ|084|Belize|BZD|Americas
CA|CAN|124|Canada|CAD|Americas
CC|CCK|166|Cocos (Keeling) Islands|AUD|Oceania
CD|COD|180|Congo, Democratic Republic of the|CDF|Africa
CF|CAF|140|Central African Republic|XAF|Africa
CG|COG|178|Congo|XAF|Africa
CH|CHE|756|Switzerland|CHF|Europe
CI|CIV|384|Côte d'Ivoire|XOF|Africa
CK|COK|184|Cook Islands|NZD|Oceania
CL|CHL|152|Chile|CLP|Americas
CM|CMR|120|Cameroon|XAF|Africa
CN|CHN|156|China|CNY|Asia
CO|COL|170|Colombia|COP|Americas
CR|CRI|188|Costa Rica|CRC|Americas
CU|CUB|192|Cuba|CUP|Americas
CV|CPV|132|Cabo Verde|CVE|Africa
CW|CUW|531|Curaçao|XCG|Americas
CX|CXR|162|Christmas Island|AUD|Oceania
CY|CYP|196|Cyprus|EUR|Asia
CZ|CZE|203|Czechia|CZK|Europe
DE|DEU|276|Germany|EUR|Europe
DJ|DJI|262|Djibouti|DJF|Africa
DK|DNK|208|Denmark|DKK|Europe
DM|DMA|212|Dominica|XCD|Americas
DO|DOM|214|Dominican Republic|DOP|Americas
DZ|DZA|012|Algeria|DZD|Africa
EC|ECU|218|Ecuador|USD|Americas
EE|EST|233|Estonia|EUR|Europe
EG|EGY|818|Egypt|EGP|Africa
EH|ESH|732|Western Sahara|MAD|Africa
ER|ERI|232|Eritrea|ERN|Africa
ES|ESP|724|Spain|EUR|Europe
ET|ETH|231|Ethiopia|ETB|Africa
FI|FIN|246|Finland|EUR|Europe
FJ|FJI|242|Fiji|FJD|Oceania
FK|FLK|238|Falkland Islands|FKP|Americas
FM|FSM|583|Micronesia|USD|Oceania
FO|FRO|234|Faroe Islands|DKK|Europe
FR|FRA|250|France|EUR|Europe
GA|GAB|266|Gabon|XAF|Africa
GB|GBR|826|United Kingdom|GBP|Europe
GD|GRD|308|Grenada|XCD|Americas
GE|GEO|268|Georgia|GEL|Asia
GF|GUF|254|French Guiana|EUR|Americas
GG|GGY|831|Guernsey|GBP|Europe
GH|GHA|288|Ghana|GHS|Africa
GI|GIB|292|Gibraltar|GIP|Europe
GL|GRL|304|Greenland|DKK|Americas
GM|GMB|270|Gambia|GMD|Africa
GN|GIN|324|Guinea|GNF|Africa
GP|GLP|312|Guadeloupe|EUR|Americas
GQ|GNQ|226|Equatorial Guinea|XAF|Africa
GR|GRC|300|Greece|EUR|Europe
GS|SGS|239|South Georgia and the South Sandwich Islands|GBP|Antarctica
GT|GTM|320|Guatemala|GTQ|Americas
GU|GUM|316|Guam|USD|Oceania
GW|GNB|624|Guinea-Bissau|XOF|Africa
GY|GUY|328|Guyana|GYD|Americas
HK|HKG|344|Hong Kong|HKD|Asia
HM|HMD|334|Heard Island and McDonald Islands|AUD|Antarctica
HN|HND|340|Honduras|HNL|Americas
HR|HRV|191|Croatia|EUR|Europe
HT|HTI|332|Haiti|HTG|Americas
HU|HUN|348|Hungary|HUF|Europe
ID|IDN|360|Indonesia|IDR|Asia
IE|IRL|372|Ireland|EUR|Europe
IL|ISR|376|Israel|ILS|Asia
IM|IMN|833|Isle of Man|GBP|Europe
IN|IND|356|India|INR|Asia
IO|IOT|086|British Indian Ocean Territory|USD|Asia
IQ|IRQ|368|Iraq|IQD|Asia
IR|IRN|364|Iran|IRR|Asia
IS|ISL|352|Iceland|ISK|Europe
IT|ITA|380|Italy|EUR|Europe
JE|JEY|832|Jersey|GBP|Europe
JM|JAM|388|Jamaica|JMD|Americas
JO|JOR|400|Jordan|JOD|Asia
JP|JPN|392|Japan|JPY|Asia
KE|KEN|404|Kenya|KES|Africa
KG|KGZ|417|Kyrgyzstan|KGS|Asia
KH|KHM|116|Cambodia|KHR|Asia
KI|KIR|296|Kiribati|AUD|Oceania
KM|COM|174|Comoros|KMF|Africa
KN|KNA|659|Saint Kitts and Nevis|XCD|Americas
KP|PRK|408|Korea, Democratic People's Republic of|KPW|Asia
KR|KOR|410|Korea, Republic of|KRW|Asia
KW|KWT|414|Kuwait|KWD|Asia
KY|CYM|136|Cayman Islands|KYD|Americas
KZ|KAZ|398|Kazakhstan|KZT|Asia
LA|LAO|418|Lao People's Democratic Republic|LAK|Asia
LB|LBN|422|Lebanon|LBP|Asia
LC|LCA|662|Saint Lucia|XCD|Americas
LI|LIE|438|Liechtenstein|CHF|Europe
LK|LKA|144|Sri Lanka|LKR|Asia
LR|LBR|430|Liberia|LRD|Africa
LS|LSO|426|Lesotho|LSL|Africa
LT|LTU|440|Lithuania|EUR|Europe
LU|LUX|442|Luxembourg|EUR|Europe
LV|LVA|428|Latvia|EUR|Europe
LY|LBY|434|Libya|LYD|Africa
MA|MAR|504|Morocco|MAD|Africa
MC|MCO|492|Monaco|EUR|Europe
MD|MDA|498|Moldova|MDL|Europe
ME|MNE|499|Montenegro|EUR|Europe
MF|MAF|663|Saint Martin (French part)|EUR|Americas
MG|MDG|450|Madagascar|MGA|Africa
MH|MHL|584|Marshall Islands|USD|Oceania
MK|MKD|807|North Macedonia|MKD|Europe
ML|MLI|466|Mali|XOF|Africa
MM|MMR|104|Myanmar|MMK|Asia
MN|MNG|496|Mongolia|MNT|Asia
MO|MAC|446|Macao|MOP|Asia
MP|MNP|580|Northern Mariana Islands|USD|Oceania
MQ|MTQ|474|Martinique|EUR|Americas
MR|MRT|478|Mauritania|MRU|Africa
MS|MSR|500|Montserrat|XCD|Americas
MT|MLT|470|Malta|EUR|Europe
MU|MUS|480|Mauritius|MUR|Africa
MV|MDV|462|Maldives|MVR|Asia
MW|MWI|454|Malawi|MWK|Africa
MX|MEX|484|Mexico|MXN|Americas
MY|MYS|458|Malaysia|MYR|Asia
MZ|MOZ|508|Mozambique|MZN|Africa
NA|NAM|516|Namibia|NAD|Africa
NC|NCL|540|New Caledonia|XPF|Oceania
NE|NER|562|Niger|XOF|Africa
NF|NFK|574|Norfolk Island|AUD|Oceania
NG|NGA|566|Nigeria|NGN|Africa
NI|NIC|558|Nicaragua|NIO|Americas
NL|NLD|528|Netherlands|EUR|Europe
NO|NOR|578|Norway|NOK|Europe
NP|NPL|524|Nepal|NPR|Asia
NR|NRU|520|Nauru|AUD|Oceania
NU|NIU|570|Niue|NZD|Oceania
NZ|NZL|554|New Zealand|NZD|Oceania
OM|OMN|512|Oman|OMR|Asia
PA|PAN|591|Panama|PAB|Americas
PE|PER|604|Peru|PEN|Americas
PF|PYF|258|French Polynesia|XPF|Oceania
PG|PNG|598|Papua New Guinea|PGK|Oceania
PH|PHL|608|Philippines|PHP|Asia
PK|PAK|586|Pakistan|PKR|Asia
PL|POL|616|Poland|PLN|Europe
PM|SPM|666|Saint Pierre and Miquelon|EUR|Americas
PN|PCN|612|Pitcairn|NZD|Oceania
PR|PRI|630|Puerto Rico|USD|Americas
PS|PSE|275|Palestine, State of|ILS|Asia
PT|PRT|620|Portugal|EUR|Europe
PW|PLW|585|Palau|USD|Oceania
PY|PRY|600|Paraguay|PYG|Americas
QA|QAT|634|Qatar|QAR|Asia
RE|REU|638|Réunion|EUR|Africa
RO|ROU|642|Romania|RON|Europe
RS|SRB|688|Serbia|RSD|Europe
RU|RUS|643|Russian Federation|RUB|Europe
RW|RWA|646|Rwanda|RWF|Africa
SA|SAU|682|Saudi Arabia|SAR|Asia
SB|SLB|090|Solomon Islands|SBD|Oceania
SC|SYC|690|Seychelles|SCR|Africa
SD|SDN|729|Sudan|SDG|Africa
SE|SWE|752|Sweden|SEK|Europe
SG|SGP|702|Singapore|SGD|Asia
SH|SHN|654|Saint Helena, Ascension and Tristan da Cunha|SHP|Africa
SI|SVN|705|Slovenia|EUR|Europe
SJ|SJM|744|Svalbard and Jan Mayen|NOK|Europe
SK|SVK|703|Slovakia|EUR|Europe
SL|SLE|694|Sierra Leone|SLE|Africa
SM|SMR|674|San Marino|EUR|Europe
SN|SEN|686|Senegal|XOF|Africa
SO|SOM|706|Somalia|SOS|Africa
SR|SUR|740|Suriname|SRD|Americas
SS|SSD|728|South Sudan|SSP|Africa
ST|STP|678|Sao Tome and Principe|STN|Africa
SV|SLV|222|El Salvador|USD|Americas
SX|SXM|534|Sint Maarten (Dutch part)|XCG|Americas
SY|SYR|760|Syrian Arab Republic|SYP|Asia
SZ|SWZ|748|Eswatini|SZL|Africa
TC|TCA|796|Turks and Caicos Islands|USD|Americas
TD|TCD|148|Chad|XAF|Africa
TF|ATF|260|French Southern Territories|EUR|Antarctica
TG|TGO|768|Togo|XOF|Africa
TH|THA|764|Thailand|THB|Asia
TJ|TJK|762|Tajikistan|TJS|Asia
TK|TKL|772|Tokelau|NZD|Oceania
TL|TLS|626|Timor-Leste|USD|Asia
TM|TKM|795|Turkmenistan|TMT|Asia
TN|TUN|788|Tunisia|TND|Africa
TO|TON|776|Tonga|TOP|Oceania
TR|TUR|792|Türkiye|TRY|Asia
TT|TTO|780|Trinidad and Tobago|TTD|Americas
TV|TUV|798|Tuvalu|AUD|Oceania
TW|TWN|158|Taiwan|TWD|Asia
TZ|TZA|834|Tanzania|TZS|Africa
UA|UKR|804|Ukraine|UAH|Europe
UG|UGA|800|Uganda|UGX|Africa
UM|UMI|581|United States Minor Outlying Islands|USD|Oceania
US|USA|840|United States|USD|Americas
UY|URY|858|Uruguay|UYU|Americas
UZ|UZB|860|Uzbekistan|UZS|Asia
VA|VAT|336|Holy See|EUR|Europe
VC|VCT|670|Saint Vincent and the Grenadines|XCD|Americas
VE|VEN|862|Venezuela|VES|Americas
VG|VGB|092|Virgin Islands, British|USD|Americas
VI|VIR|850|Virgin Islands, U.S.|USD|Americas
VN|VNM|704|Viet Nam|VND|Asia
VU|VUT|548|Vanuatu|VUV|Oceania
WF|WLF|876|Wallis and Futuna|XPF|Oceania
WS|WSM|882|Samoa|WST|Oceania
YE|YEM|887|Yemen|YER|Asia
YT|MYT|175|Mayotte|EUR|Africa
ZA|ZAF|710|South Africa|ZAR|Africa
ZM|ZMB|894|Zambia|ZMW|Africa
ZW|ZWE|716|Zimbabwe|ZWG|Africa
"""


def _parse_countries() -> tuple[CountryRecord, ...]:
    records = []
    for line in _COUNTRY_DATA.strip().splitlines():
        parts = line.split("|")
        if len(parts) != 6:  # pragma: no cover - guards against edit mistakes
            continue
        iso2, iso3, numeric, name, currency, region = parts
        records.append(CountryRecord(iso2, iso3, numeric, name, currency, region))
    return tuple(records)


COUNTRIES: Final[tuple[CountryRecord, ...]] = _parse_countries()

BY_ISO2: Final[dict[str, CountryRecord]] = {record.iso2: record for record in COUNTRIES}
BY_ISO3: Final[dict[str, CountryRecord]] = {record.iso3: record for record in COUNTRIES}
BY_NUMERIC: Final[dict[str, CountryRecord]] = {record.numeric: record for record in COUNTRIES}

#: Common informal names and historic spellings mapped to their ISO alpha-2.
COUNTRY_ALIASES: Final[dict[str, str]] = {
    "USA": "US",
    "U.S.A.": "US",
    "U.S.": "US",
    "UNITED STATES OF AMERICA": "US",
    "AMERICA": "US",
    "UK": "GB",
    "U.K.": "GB",
    "GREAT BRITAIN": "GB",
    "ENGLAND": "GB",
    "SCOTLAND": "GB",
    "WALES": "GB",
    "NORTHERN IRELAND": "GB",
    "BRITAIN": "GB",
    "SOUTH KOREA": "KR",
    "REPUBLIC OF KOREA": "KR",
    "NORTH KOREA": "KP",
    "RUSSIA": "RU",
    "IVORY COAST": "CI",
    "COTE DIVOIRE": "CI",
    "CAPE VERDE": "CV",
    "CZECH REPUBLIC": "CZ",
    "SWAZILAND": "SZ",
    "BURMA": "MM",
    "HOLLAND": "NL",
    "THE NETHERLANDS": "NL",
    "UAE": "AE",
    "U.A.E.": "AE",
    "VATICAN": "VA",
    "VATICAN CITY": "VA",
    "TURKEY": "TR",
    "MACEDONIA": "MK",
    "VIETNAM": "VN",
    "LAOS": "LA",
    "BOLIVIA PLURINATIONAL STATE OF": "BO",
    "MOLDOVA REPUBLIC OF": "MD",
    "TANZANIA UNITED REPUBLIC OF": "TZ",
    "SYRIA": "SY",
    "BRUNEI": "BN",
    "MICRONESIA FEDERATED STATES OF": "FM",
    "PALESTINE": "PS",
    "TAIWAN PROVINCE OF CHINA": "TW",
    "HONG KONG SAR": "HK",
    "MACAU": "MO",
    "DRC": "CD",
    "CONGO KINSHASA": "CD",
    "CONGO BRAZZAVILLE": "CG",
    # Endonyms that appear regularly in issuer datasets.
    "DEUTSCHLAND": "DE",
    "ESPANA": "ES",
    "ITALIA": "IT",
    "NIPPON": "JP",
    "NIHON": "JP",
    "BRASIL": "BR",
    "MEXICO": "MX",
    "SUISSE": "CH",
    "SCHWEIZ": "CH",
    "SVIZZERA": "CH",
    "SVERIGE": "SE",
    "NORGE": "NO",
    "DANMARK": "DK",
    "SUOMI": "FI",
    "NEDERLAND": "NL",
    "OSTERREICH": "AT",
    "POLSKA": "PL",
    "CESKA REPUBLIKA": "CZ",
    "CESKO": "CZ",
    "MAGYARORSZAG": "HU",
    "ELLADA": "GR",
    "HELLAS": "GR",
    "PORTUGAL": "PT",
    "EIRE": "IE",
}

#: ISO 3166-2 subdivisions for the markets where issuer addresses are most
#: often recorded with a state/province code rather than a full name.
US_STATES: Final[dict[str, str]] = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii",
    "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa",
    "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine",
    "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska",
    "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico",
    "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio",
    "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island",
    "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas",
    "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming", "PR": "Puerto Rico",
    "VI": "U.S. Virgin Islands", "GU": "Guam", "AS": "American Samoa",
    "MP": "Northern Mariana Islands",
}

CA_PROVINCES: Final[dict[str, str]] = {
    "AB": "Alberta", "BC": "British Columbia", "MB": "Manitoba",
    "NB": "New Brunswick", "NL": "Newfoundland and Labrador",
    "NS": "Nova Scotia", "NT": "Northwest Territories", "NU": "Nunavut",
    "ON": "Ontario", "PE": "Prince Edward Island", "QC": "Quebec",
    "SK": "Saskatchewan", "YT": "Yukon",
}

AU_STATES: Final[dict[str, str]] = {
    "ACT": "Australian Capital Territory", "NSW": "New South Wales",
    "NT": "Northern Territory", "QLD": "Queensland", "SA": "South Australia",
    "TAS": "Tasmania", "VIC": "Victoria", "WA": "Western Australia",
}

SUBDIVISIONS: Final[dict[str, dict[str, str]]] = {
    "US": US_STATES,
    "CA": CA_PROVINCES,
    "AU": AU_STATES,
}

#: Reverse lookup (name -> code) per country, built once.
SUBDIVISION_NAMES: Final[dict[str, dict[str, str]]] = {
    country: {name.upper(): code for code, name in table.items()}
    for country, table in SUBDIVISIONS.items()
}

#: Corporate/legal suffixes stripped when normalising an institution name.
LEGAL_SUFFIXES: Final[tuple[str, ...]] = (
    "national association", "n a", "na", "nat assn",
    "incorporated", "inc", "corporation", "corp", "company", "co",
    "limited", "ltd", "llc", "llp", "lp", "plc", "pllc",
    "public limited company", "public joint stock company", "pjsc", "jsc",
    "société anonyme", "societe anonyme", "sa", "s a", "sas", "sarl", "sasu",
    "aktiengesellschaft", "ag", "gmbh", "kgaa", "se",
    "naamloze vennootschap", "nv", "n v", "bv", "b v",
    "aktiebolag", "ab", "as", "asa", "oyj", "oy", "aps",
    "spa", "s p a", "srl", "s r l", "sapi de cv", "sa de cv", "sab de cv",
    "pte", "pte ltd", "sdn bhd", "bhd", "tbk", "pt",
    "pjsc", "ojsc", "zao", "pao", "ooo",
    "cooperative", "co operative", "coop",
    "holdings", "holding", "group", "groupe", "gruppo", "grupo",
    "bankaktiengesellschaft",
)

#: Words that carry no discriminating power when comparing institution names.
STOPWORDS: Final[frozenset[str]] = frozenset(
    {
        "the", "of", "and", "for", "a", "an", "de", "la", "le", "el", "du",
        "der", "die", "das", "van", "von", "di", "del",
    }
)

#: Single-token abbreviations expanded during normalization, so that
#: "Northshore CU" and "Northshore Credit Union" reach the same canonical form.
#: Only exact whole-token matches are expanded — never substrings.
ABBREVIATIONS: Final[dict[str, str]] = {
    "cu": "credit union",
    "fcu": "federal credit union",
    "cus": "credit union",
    "nb": "national bank",
    "natl": "national",
    "nat": "national",
    "intl": "international",
    "int": "international",
    "sb": "savings bank",
    "svgs": "savings",
    "svcs": "services",
    "svc": "service",
    "fin": "financial",
    "fincl": "financial",
    "fed": "federal",
    "mut": "mutual",
    "assn": "association",
    "assoc": "association",
    "coop": "cooperative",
    "bk": "bank",
    "bnk": "bank",
    "bcp": "bancorp",
    "tr": "trust",
    "mgmt": "management",
    "hldgs": "holdings",
    "grp": "group",
}

#: Generic banking words: kept in the normalised name (they are part of the
#: institution's identity) but weighted down when scoring a name match.
GENERIC_TOKENS: Final[frozenset[str]] = frozenset(
    {
        "bank", "banco", "banque", "banca", "bankas", "banka", "bancorp",
        "credit", "union", "savings", "trust", "financial", "finance",
        "national", "international", "federal", "state", "first", "commercial",
        "cooperative", "mutual", "card", "cards", "services", "solutions",
        "payments", "payment", "sparkasse", "raiffeisen", "volksbank",
    }
)
