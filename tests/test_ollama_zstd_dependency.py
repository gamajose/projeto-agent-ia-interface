from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_ollama_installs_zstd_before_official_installer() -> None:
    content = (PROJECT_ROOT / "scripts" / "setup_ollama.sh").read_text(encoding="utf-8")

    assert "ensure_zstd()" in content
    assert "apt-get install -y zstd" in content
    assert "dnf install -y zstd" in content
    assert "yum install -y zstd" in content
    assert "zypper --non-interactive install zstd" in content
    assert "pacman -Sy --noconfirm zstd" in content
    assert content.index("ensure_zstd\n") < content.index("install_ollama\n")
