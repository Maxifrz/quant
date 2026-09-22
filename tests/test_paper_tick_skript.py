"""`scripts/paper_tick.sh` gegen ein echtes Git-Remote, ohne Netz und ohne `uv`.

Das Skript ist die einzige Stelle, an der der Kontostand den Container
ueberlebt, und bis ADR-079 lief es ungetestet. Zwei seiner Fehler sahen
nicht nach Fehlern aus:

1. Nach dem Push nach `main` hatte der Session-Branch keinen Upstream. Ein
   Pruefhaken der Sitzung meldete "unpushed commit", der Agent pushte den
   Branch -- zehn Branches in zwoelf Tagen, jeder ohne eigenen Inhalt.
2. Nach dem Rueckfall auf den eigenen Branch meldete das Skript trotzdem
   "Kontostand auf main gesichert".

Das Remote ist ein nacktes Repository im Temp-Verzeichnis, `uv` ein Skript,
das eine Kontodatei schreibt. Die globale Git-Konfiguration wird
ausgeblendet: Commit-Signaturen und Proxy-Einstellungen der Umgebung haben
in diesem Test nichts zu suchen.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

SKRIPT = Path(__file__).resolve().parents[1] / "scripts" / "paper_tick.sh"
SESSION = "claude/lucid-meitner-test"

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None or shutil.which("bash") is None,
    reason="git und bash noetig",
)


def _ausfuehrbar(pfad: Path, inhalt: str) -> None:
    pfad.write_text(inhalt)
    pfad.chmod(pfad.stat().st_mode | stat.S_IEXEC)


@pytest.fixture
def umgebung(tmp_path: Path) -> dict[str, str]:
    gitconfig = tmp_path / "gitconfig"
    gitconfig.write_text(
        "[user]\n\tname = Test\n\temail = test@example.invalid\n"
        "[commit]\n\tgpgsign = false\n"
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    # Ein Tick, der den Kontostand fortschreibt. `QT_TEST_KEIN_TICK` laesst
    # ihn nichts tun -- der Fall "keine neuen Bars".
    _ausfuehrbar(
        bin_dir / "uv",
        "#!/usr/bin/env bash\n"
        '[ -n "${QT_TEST_KEIN_TICK:-}" ] && exit 0\n'
        "mkdir -p data/paper\n"
        'echo "{\\"tick\\": \\"$RANDOM$RANDOM\\"}" >> data/paper/konto.json\n',
    )
    # Die Push-Schleife wartet 2+4+8+16 Sekunden. Im Test nicht.
    _ausfuehrbar(bin_dir / "sleep", "#!/usr/bin/env bash\nexit 0\n")
    env = {
        k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "QT_TEST_"))
    }
    env.update(
        PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        GIT_CONFIG_GLOBAL=str(gitconfig),
        GIT_CONFIG_NOSYSTEM="1",
        HOME=str(tmp_path),
    )
    return env


def _git(cwd: Path, env: dict[str, str], *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def checkout(tmp_path: Path, umgebung: dict[str, str]) -> Path:
    """Wie eine frische Routine-Sitzung: Session-Branch auf `main`, ohne Upstream."""
    remote = tmp_path / "remote.git"
    _git(tmp_path, umgebung, "init", "-q", "--bare", "-b", "main", str(remote))

    saat = tmp_path / "saat"
    _git(tmp_path, umgebung, "init", "-q", "-b", "main", str(saat))
    (saat / "scripts").mkdir()
    shutil.copy(SKRIPT, saat / "scripts" / "paper_tick.sh")
    (saat / "data" / "paper").mkdir(parents=True)
    (saat / "data" / "paper" / "konto.json").write_text("{}\n")
    _git(saat, umgebung, "add", "-A")
    _git(saat, umgebung, "commit", "-q", "-m", "Anfang")
    _git(saat, umgebung, "remote", "add", "origin", str(remote))
    _git(saat, umgebung, "push", "-q", "origin", "main")

    arbeit = tmp_path / "arbeit"
    _git(tmp_path, umgebung, "clone", "-q", str(remote), str(arbeit))
    _git(arbeit, umgebung, "checkout", "-q", "-b", SESSION, "origin/main")
    _git(arbeit, umgebung, "branch", "-q", "--unset-upstream")
    return arbeit


def _tick(arbeit: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "scripts/paper_tick.sh"],
        cwd=arbeit,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


# Ergebnisse vor dem `assert` in Variablen: sonst zeigt pytest bei einem
# Fehlschlag die Argumente -- und `env` ist die komplette Umgebung.
def _upstream(arbeit: Path, env: dict[str, str]) -> str | None:
    lauf = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", f"{SESSION}@{{u}}"],
        cwd=arbeit,
        env=env,
        capture_output=True,
        text=True,
    )
    return lauf.stdout.strip() if lauf.returncode == 0 else None


def _remote_branches(arbeit: Path, env: dict[str, str]) -> set[str]:
    zeilen = _git(arbeit, env, "ls-remote", "--heads", "origin").splitlines()
    return {z.split("refs/heads/", 1)[1] for z in zeilen if z}


def test_nach_dem_push_nach_main_gibt_es_nichts_mehr_zu_pushen(checkout, umgebung):
    vorher = _git(checkout, umgebung, "rev-parse", "origin/main")

    lauf = _tick(checkout, umgebung)

    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    nachher = _git(checkout, umgebung, "ls-remote", "origin", "refs/heads/main")
    assert not nachher.startswith(vorher), "Kontostand nicht in main angekommen"
    upstream = _upstream(checkout, umgebung)
    assert upstream == "origin/main"
    # Genau das, was der Pruefhaken der Sitzung fragt.
    voraus = _git(checkout, umgebung, "rev-list", "@{u}..HEAD")
    assert voraus == ""
    branches = _remote_branches(checkout, umgebung)
    assert branches == {"main"}
    assert "Kontostand auf main gesichert." in lauf.stdout


def test_ohne_neuen_kontostand_verfolgt_der_branch_trotzdem_main(checkout, umgebung):
    lauf = _tick(checkout, umgebung | {"QT_TEST_KEIN_TICK": "1"})

    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    assert "nichts zu sichern" in lauf.stdout
    upstream = _upstream(checkout, umgebung)
    assert upstream == "origin/main"
    branches = _remote_branches(checkout, umgebung)
    assert branches == {"main"}


def test_rueckfall_auf_den_branch_meldet_den_branch_und_nicht_main(
    checkout, umgebung, tmp_path
):
    # Ein geschuetztes `main`: das Remote lehnt jeden Push darauf ab.
    _ausfuehrbar(
        tmp_path / "remote.git" / "hooks" / "pre-receive",
        "#!/usr/bin/env bash\n"
        "while read alt neu ref; do\n"
        '  [ "$ref" = refs/heads/main ] && { echo "main ist geschuetzt"; exit 1; }\n'
        "done\n"
        "exit 0\n",
    )

    lauf = _tick(checkout, umgebung)

    assert lauf.returncode == 0, lauf.stdout + lauf.stderr
    assert "Rueckfall auf" in lauf.stdout
    assert f"Kontostand auf {SESSION} gesichert." in lauf.stdout
    assert "auf main gesichert" not in lauf.stdout
    # Hier liegt der Zustand wirklich nur auf dem Branch -- also darf der
    # Branch nicht so tun, als verfolge er `main`.
    upstream = _upstream(checkout, umgebung)
    assert upstream == f"origin/{SESSION}"
    branches = _remote_branches(checkout, umgebung)
    assert branches == {"main", SESSION}


def test_ganz_gescheiterter_push_meldet_kein_gesichert(checkout, umgebung, tmp_path):
    _ausfuehrbar(
        tmp_path / "remote.git" / "hooks" / "pre-receive",
        "#!/usr/bin/env bash\necho gesperrt\nexit 1\n",
    )

    lauf = _tick(checkout, umgebung)

    assert lauf.returncode == 1
    assert "gesichert." not in lauf.stdout
    assert "Vier Push-Versuche auf main gescheitert" in lauf.stdout
