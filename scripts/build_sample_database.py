#!/usr/bin/env python3
"""Build a synthetic Bin-Tel database package plus its manifest.

This is the developer/administrator counterpart to the production
distribution server: it produces a real ``.sqlite`` package and a
``database-manifest.json`` next to it, which the desktop application can
install through the ordinary first-run and update flows (point the manifest
URL at the file, or serve the directory with ``scripts/serve_database.py``).

Every record is generated. No real cardholder data is involved, and the issuer
names are invented institutions.

    python scripts/build_sample_database.py --output dist/database --bins 5000
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.constants import SCHEMA_VERSION  # noqa: E402
from app.database.engine import DatabaseManager  # noqa: E402
from app.database.schema import analyze, create_schema, write_metadata  # noqa: E402
from app.models.entities import DatabaseMetadata, DatabaseVersion  # noqa: E402
from app.services.ingest_service import IngestService, RawBinRecord  # noqa: E402
from app.providers.compression import compress, normalise, suffix_for  # noqa: E402
from app.utils.hashing import file_checksum  # noqa: E402

# --- synthetic issuer catalogue -------------------------------------------
# Invented institutions; any resemblance to a real bank's name is incidental.
ISSUERS: list[tuple[str, str, str, str, str, str, str]] = [
    # display, legal, country, state, city, postal, website
    ("Meridian Trust Bank", "Meridian Trust Bank, N.A.", "US", "NY", "New York", "10001", "meridiantrust.example"),
    ("Cascade Federal Credit Union", "Cascade Federal Credit Union", "US", "WA", "Seattle", "98104", "cascadefcu.example"),
    ("Lonestar Commerce Bank", "Lonestar Commerce Bank, N.A.", "US", "TX", "Austin", "73301", "lonestarcommerce.example"),
    ("Harborview Savings", "Harborview Savings Bank", "US", "MA", "Boston", "02110", "harborview.example"),
    ("Sierra Pacific Bank", "Sierra Pacific Bank", "US", "CA", "San Francisco", "94105", "sierrapacific.example"),
    ("Northgate Financial", "Northgate Financial Corporation", "US", "IL", "Chicago", "60601", "northgatefin.example"),
    ("Maple Ridge Bank", "Maple Ridge Bank of Canada", "CA", "ON", "Toronto", "M5H 2N2", "mapleridge.example"),
    ("Prairie Union Bank", "Prairie Union Bank", "CA", "AB", "Calgary", "T2P 1J9", "prairieunion.example"),
    ("Thames & Crown Bank", "Thames and Crown Banking plc", "GB", "", "London", "EC2R 8AH", "thamescrown.example"),
    ("Northern Isles Bank", "Northern Isles Banking Group plc", "GB", "", "Edinburgh", "EH2 2YE", "northernisles.example"),
    ("Rheinbrücke Bank", "Rheinbrücke Bank AG", "DE", "", "Frankfurt am Main", "60311", "rheinbruecke.example"),
    ("Nordlicht Sparbank", "Nordlicht Sparbank eG", "DE", "", "Hamburg", "20095", "nordlicht.example"),
    ("Banque du Littoral", "Banque du Littoral S.A.", "FR", "", "Paris", "75008", "littoral.example"),
    ("Banco del Altiplano", "Banco del Altiplano, S.A.", "ES", "", "Madrid", "28046", "altiplano.example"),
    ("Banca Ligure", "Banca Ligure S.p.A.", "IT", "", "Milano", "20121", "bancaligure.example"),
    ("Amstel Handelsbank", "Amstel Handelsbank N.V.", "NL", "", "Amsterdam", "1017 CG", "amstelhandels.example"),
    ("Fjord Sparebank", "Fjord Sparebank ASA", "NO", "", "Oslo", "0150", "fjordspare.example"),
    ("Vasa Finansbank", "Vasa Finansbank AB", "SE", "", "Stockholm", "111 47", "vasafinans.example"),
    ("Sakura Shinwa Bank", "Sakura Shinwa Bank, Limited", "JP", "", "Tokyo", "100-0005", "sakurashinwa.example"),
    ("Pacific Rim Bank", "Pacific Rim Banking Corporation", "SG", "", "Singapore", "049513", "pacificrim.example"),
    ("Coral Bay Bank", "Coral Bay Bank Limited", "AU", "NSW", "Sydney", "2000", "coralbay.example"),
    ("Southern Cross Mutual", "Southern Cross Mutual Bank Limited", "AU", "VIC", "Melbourne", "3000", "southerncross.example"),
    ("Banco Aurora do Sul", "Banco Aurora do Sul S.A.", "BR", "", "São Paulo", "01310-100", "aurorasul.example"),
    ("Banco Andino Unido", "Banco Andino Unido S.A.", "CO", "", "Bogotá", "110111", "andinounido.example"),
    ("Banco del Río Plata", "Banco del Río Plata S.A.", "AR", "", "Buenos Aires", "C1001", "rioplata.example"),
    ("Nilo Commercial Bank", "Nilo Commercial Bank S.A.E.", "EG", "", "Cairo", "11511", "nilocommercial.example"),
    ("Savanna Union Bank", "Savanna Union Bank Plc", "NG", "", "Lagos", "101233", "savannaunion.example"),
    ("Table Bay Bank", "Table Bay Bank Limited", "ZA", "", "Cape Town", "8001", "tablebay.example"),
    ("Gulf Crescent Bank", "Gulf Crescent Bank P.J.S.C.", "AE", "", "Dubai", "00000", "gulfcrescent.example"),
    ("Anatolia Halk Bankasi", "Anatolia Halk Bankasi A.S.", "TR", "", "Istanbul", "34394", "anatoliahalk.example"),
    ("Ganges Commercial Bank", "Ganges Commercial Bank Limited", "IN", "", "Mumbai", "400001", "gangescommercial.example"),
    ("Silk Road Bank", "Silk Road Bank Co., Ltd.", "CN", "", "Shanghai", "200120", "silkroadbank.example"),
    ("Han River Bank", "Han River Bank Co., Ltd.", "KR", "", "Seoul", "04524", "hanriver.example"),
    ("Mekong Delta Bank", "Mekong Delta Commercial Bank", "VN", "", "Ho Chi Minh City", "700000", "mekongdelta.example"),
    ("Andes Altura Banco", "Andes Altura Banco S.A.", "CL", "", "Santiago", "8320000", "andesaltura.example"),
    ("Baltic Vega Bank", "Baltic Vega Bank AS", "EE", "", "Tallinn", "10145", "balticvega.example"),
    ("Carpathia Bank", "Carpathia Bank S.A.", "RO", "", "Bucharest", "010171", "carpathia.example"),
    ("Vistula Bank Polski", "Vistula Bank Polski S.A.", "PL", "", "Warszawa", "00-950", "vistulabank.example"),
    ("Helvetia Alpenbank", "Helvetia Alpenbank AG", "CH", "", "Zürich", "8001", "helvetiaalpen.example"),
    ("Emerald Isle Bank", "Emerald Isle Bank Designated Activity Company", "IE", "", "Dublin", "D02 XY45", "emeraldisle.example"),
]

PARENTS = {
    "Cascade Federal Credit Union": "Northgate Financial",
    "Harborview Savings": "Meridian Trust Bank",
    "Nordlicht Sparbank": "Rheinbrücke Bank",
    "Southern Cross Mutual": "Coral Bay Bank",
    "Prairie Union Bank": "Maple Ridge Bank",
}

NETWORK_PREFIXES: list[tuple[str, str]] = [
    ("visa", "4"),
    ("mastercard", "5"),
    ("mastercard", "2"),
    ("amex", "37"),
    ("discover", "65"),
    ("jcb", "35"),
    ("unionpay", "62"),
    ("diners", "36"),
    ("maestro", "67"),
]

CARD_TYPES = ["credit", "credit", "credit", "debit", "debit", "debit", "prepaid", "charge"]
STATUSES = ["active"] * 18 + ["inactive", "retired"]


def build(
    output_dir: Path,
    bin_count: int,
    version: str,
    seed: int,
    compression: str = "none",
) -> tuple[Path, Path]:
    rng = random.Random(seed)
    output_dir.mkdir(parents=True, exist_ok=True)
    package = output_dir / f"bintel-{version}.sqlite"
    package.unlink(missing_ok=True)
    for suffix in ("-wal", "-shm"):
        package.with_name(package.name + suffix).unlink(missing_ok=True)

    manager = DatabaseManager(package)
    manager.open(create_if_missing=True)
    create_schema(manager.engine)

    seen: set[str] = set()
    written = 0
    with manager.transaction() as session:
        ingest = IngestService(
            session, source_code="bintel-reference", source_name="Bin-Tel reference data"
        )
        ingest.seed_reference_data()

        while written < bin_count:
            issuer = ISSUERS[rng.randrange(len(ISSUERS))]
            display, legal, country, state, city, postal, website = issuer
            network, prefix = NETWORK_PREFIXES[rng.randrange(len(NETWORK_PREFIXES))]
            digits = prefix + "".join(str(rng.randrange(10)) for _ in range(6 - len(prefix)))
            if digits in seen:
                continue
            seen.add(digits)

            card_type = CARD_TYPES[rng.randrange(len(CARD_TYPES))]
            commercial = rng.random() < 0.18
            prepaid = card_type == "prepaid"
            brand_words = {
                "credit": ["Classic", "Gold", "Platinum", "Signature", "Infinite"],
                "debit": ["Everyday", "Classic", "Premier", "Access"],
                "prepaid": ["Reload", "Gift", "Payroll", "Travel"],
                "charge": ["Corporate", "Executive"],
            }[card_type]
            brand = f"{brand_words[rng.randrange(len(brand_words))]}"
            if commercial:
                brand = f"Business {brand}"

            record = RawBinRecord(
                bin=digits,
                network=network,
                brand=brand,
                card_type=card_type,
                prepaid="yes" if prepaid else "no",
                commercial="yes" if commercial else "no",
                issuer=display,
                issuer_legal_name=legal,
                parent_institution=PARENTS.get(display),
                website=f"https://www.{website}",
                country=country,
                state=state or None,
                city=city,
                postal_code=postal,
                address_line1=f"{rng.randrange(1, 900)} {rng.choice(['Market', 'Central', 'Harbour', 'Union', 'Cathedral'])} Street",
                status=STATUSES[rng.randrange(len(STATUSES))],
                confidence=round(rng.uniform(0.85, 1.0), 2),
            )
            ingest.ingest(record)
            written += 1
            if written % 2000 == 0:
                session.flush()
                print(f"  … {written:,}/{bin_count:,} BINs", flush=True)

        # A handful of allocated ranges, so range lookups have something to hit.
        for _ in range(min(40, max(4, bin_count // 100))):
            issuer = ISSUERS[rng.randrange(len(ISSUERS))]
            network, prefix = NETWORK_PREFIXES[rng.randrange(len(NETWORK_PREFIXES))]
            low = prefix + "".join(str(rng.randrange(10)) for _ in range(5 - len(prefix)))
            ingest.ingest(
                RawBinRecord(
                    bin=low + "0",
                    bin_high=low + "9",
                    network=network,
                    card_type="credit",
                    issuer=issuer[0],
                    issuer_legal_name=issuer[1],
                    country=issuer[2],
                    state=issuer[3] or None,
                    city=issuer[4],
                    postal_code=issuer[5],
                    status="active",
                    confidence=0.9,
                )
            )

        session.add(
            DatabaseVersion(
                version=version,
                schema_version=SCHEMA_VERSION,
                edition="community",
                release_date=datetime.now(UTC),
                record_count=written,
                institution_count=len(ISSUERS),
                notes="Synthetic reference package for development and testing.",
            )
        )
        write_metadata(
            session,
            {
                DatabaseMetadata.VERSION: version,
                DatabaseMetadata.SCHEMA_VERSION: SCHEMA_VERSION,
                DatabaseMetadata.RELEASE_DATE: datetime.now(UTC).isoformat(),
                DatabaseMetadata.RECORD_COUNT: written,
                DatabaseMetadata.PUBLISHER: "Bin-Tel Project",
                DatabaseMetadata.BUILD_ID: f"sample-{seed}",
                DatabaseMetadata.NOTES: "Synthetic reference package for development and testing.",
            },
        )

    analyze(manager.engine)
    manager.close()

    for suffix in ("-wal", "-shm"):
        package.with_name(package.name + suffix).unlink(missing_ok=True)

    database_size = package.stat().st_size

    # The published checksum covers the artefact that is actually transferred.
    compression = normalise(compression)
    if compression == "none":
        artefact = package
    else:
        artefact = package.with_name(package.name + suffix_for(compression))
        compress(package, artefact, compression)

    digest = file_checksum(artefact, "sha256")
    manifest_path = output_dir / "database-manifest.json"
    manifest = {
        "version": version,
        "schema_version": SCHEMA_VERSION,
        "min_schema_version": 1,
        "release_date": datetime.now(UTC).isoformat(),
        "database_size": database_size,
        "compressed_size": artefact.stat().st_size if compression != "none" else 0,
        "record_count": written,
        "institution_count": len(ISSUERS),
        "sha256": digest,
        "download_url": artefact.name,
        "compression": compression,
        "edition": "community",
        "publisher": "Bin-Tel Project",
        "notes": "Synthetic reference package for development and testing.",
        "minimum_app_version": "1.0.0",
        "deltas": [],
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return artefact, manifest_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a synthetic Bin-Tel database package.")
    parser.add_argument("--output", type=Path, default=Path("dist/database"))
    parser.add_argument("--bins", type=int, default=5000, help="number of BIN records")
    parser.add_argument(
        "--version",
        default=datetime.now(UTC).strftime("%Y.%m.%d"),
        help="package version (default: today's date)",
    )
    parser.add_argument("--seed", type=int, default=20260814)
    parser.add_argument(
        "--compression",
        default="none",
        choices=["none", "gzip", "xz", "bz2"],
        help="compress the published package",
    )
    args = parser.parse_args(argv)

    print(f"Building a {args.bins:,}-BIN sample package…")
    package, manifest = build(
        args.output, args.bins, args.version, args.seed, args.compression
    )
    size_mb = package.stat().st_size / (1024 * 1024)
    print(f"\nPackage:  {package}  ({size_mb:.1f} MB)")
    print(f"Manifest: {manifest}")
    print(f"\nPoint Bin-Tel at it with:\n  BINTEL_MANIFEST_URL={manifest.resolve().as_uri()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
