# Install on a clean Linux collector

Supported baseline: a maintained Debian/Ubuntu-family Linux with systemd user
services, Python 3.9 or newer, Git, curl, jq, CA certificates, SQLite, and at
least 1 GiB free beyond the estimated archive and backup staging size.

```sh
sudo apt update
sudo apt install git python3 python3-venv python3-pip curl jq sqlite3 ca-certificates rclone
mkdir -p "$HOME/evidence-collection"
git clone https://github.com/petesherratt-collab/evidence-archive.git "$HOME/evidence-collection/repo"
cd "$HOME/evidence-collection/repo"
git checkout <reviewed-tag-or-commit>
python3 -m venv "$HOME/evidence-collection/.venv"
"$HOME/evidence-collection/.venv/bin/pip" install --requirement requirements-deploy.lock
"$HOME/evidence-collection/.venv/bin/pip" install ./tools/kibitzr-archive
python3 -m venv "$HOME/evidence-collection/.venv-anchor"
"$HOME/evidence-collection/.venv-anchor/bin/pip" install opentimestamps-client==0.7.2
```

The run root contains `kibitzr.yml`, `.venv/`, `.venv-anchor/`, and `archive/`;
the checkout is `repo/`. Host settings live outside Git at
`~/.config/evidence-archive/environment`. rclone credentials remain in its
host-private configuration. Never copy a development virtualenv.

`requirements-deploy.txt` is the human-readable top-level declaration;
`requirements-deploy.lock` is the exact transitive Linux/Python 3.13 rehearsal
set. Regenerate and test the lock deliberately when the target Python or release
changes. Adding hashes and/or an artefact mirror remains recommended before the
first production installation.
