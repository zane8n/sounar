#!/bin/sh
# Sounar installer: per user, no sudo. Nothing here changes your sound; `sounar setup` does that,
# after asking.
#
#   ./install.sh               install or update (keeps your setup, presets and calibration)
#   ./install.sh --yes         also say yes to the optional extras (Omarchy bar icon, key bindings)
#   ./install.sh --uninstall   remove Sounar (keeps ~/.config/sounar)
#   ./install.sh --uninstall --purge   ... and your setup, presets, calibration and logs
set -u

SRC=$(cd "$(dirname "$0")" && pwd)
DATA=${XDG_DATA_HOME:-$HOME/.local/share}
CONF=${XDG_CONFIG_HOME:-$HOME/.config}
STATE=${XDG_STATE_HOME:-$HOME/.local/state}
CACHE=${XDG_CACHE_HOME:-$HOME/.cache}
DEST=$DATA/sounar
BIN=$HOME/.local/bin/sounar
UNITS="sounar-guard.service sounar-keepalive.service"
MARK="# sounar wrapper"
YES=0

say() { printf '%s\n' "$*"; }
have() { command -v "$1" >/dev/null 2>&1; }

ask() {   # ask "question" -> 0 for yes. --yes answers yes; without a terminal the answer is no.
  [ "$YES" = 1 ] && return 0
  [ -t 0 ] || return 1
  printf '%s [y/N] ' "$1"
  read -r a || return 1
  case "$a" in y|Y|yes|Yes) return 0 ;; *) return 1 ;; esac
}

ee_dir() {   # where Easy Effects keeps output presets (empty if it isn't installed)
  for d in "$DATA/easyeffects/output" "$CONF/easyeffects/output" \
           "$HOME/.var/app/com.github.wwmm.easyeffects/data/easyeffects/output"; do
    [ -d "$d" ] && { printf '%s' "$d"; return; }
  done
  if have easyeffects; then mkdir -p "$DATA/easyeffects/output" && printf '%s' "$DATA/easyeffects/output"; fi
}

check() {
  missing=""
  have python3 || missing="$missing python3"
  if have python3; then
    python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null \
      || missing="$missing python3>=3.11"
    # -I: the way the `sounar` command runs it (a pip --user NumPy isn't seen there)
    python3 -I -c 'import numpy' 2>/dev/null || missing="$missing numpy(python3)"
  fi
  for c in pipewire pw-cli pw-dump pw-play pw-record pactl busctl; do have "$c" || missing="$missing $c"; done
  if [ -n "$missing" ]; then
    say "Sounar needs:$missing"
    say "  Arch: sudo pacman -S python python-numpy pipewire pipewire-pulse wireplumber systemd"
    say "  Debian/Ubuntu: sudo apt install python3 python3-numpy pipewire pipewire-pulse wireplumber pipewire-bin"
    say "  Fedora: sudo dnf install python3 python3-numpy pipewire pipewire-pulseaudio wireplumber pipewire-utils"
    exit 1
  fi
  v=$(pipewire --version 2>/dev/null | sed -n 's/.*libpipewire \([0-9][0-9]*\)\.\([0-9][0-9]*\)\..*/\1 \2/p' | head -n 1)
  major=${v% *}; minor=${v#* }
  if [ -z "$v" ] || [ "$major" -lt 1 ] || { [ "$major" -eq 1 ] && [ "$minor" -lt 4 ]; }; then
    say "Sounar needs PipeWire 1.4 or newer (found: ${v:-unknown}); it puts its filters inside each output."
    exit 1
  fi
  have easyeffects || say "Note: Easy Effects isn't installed. Presets still arrange the speakers; install it for the EQ presets and song leveling."
  have notify-send || say "Note: notify-send isn't installed, so there will be no desktop notifications."
}

strip_block() {   # strip_block FILE COMMENT: remove the lines between "<COMMENT> sounar:begin" and ":end"
  [ -f "$1" ] || return 0
  sed "/$2 sounar:begin/,/$2 sounar:end/d" "$1" > "$1.sounar-tmp" && mv "$1.sounar-tmp" "$1"
}

install_program() {
  rm -rf "$DEST.new"
  mkdir -p "$DEST.new" "$(dirname "$BIN")"
  for p in sounar setups docs integrations README.md LICENSE install.sh; do
    [ -e "$SRC/$p" ] && cp -R "$SRC/$p" "$DEST.new/"
  done
  find "$DEST.new" -name __pycache__ -prune -exec rm -rf {} +
  cat > "$DEST.new/run.py" <<'EOF'
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
from sounar.cli import main  # noqa: E402

sys.exit(main())
EOF
  rm -rf "$DEST"
  mv "$DEST.new" "$DEST"
  if [ -e "$BIN" ] && ! grep -q "$MARK" "$BIN" 2>/dev/null; then
    old="$BIN.before-install.$(date +%s)"
    mv "$BIN" "$old"
    say "Moved your existing $BIN to $old"
  fi
  cat > "$BIN" <<EOF
#!/bin/sh
$MARK (installed by Sounar's install.sh; \`sounar uninstall\` removes it)
exec python3 -I "$DEST/run.py" "\$@"
EOF
  chmod +x "$BIN"
  say "Installed Sounar in $DEST and the command $BIN"
}

install_effects() {
  d=$(ee_dir)
  [ -n "$d" ] || return 0
  for f in "$SRC"/sounar/data/easyeffects/*.json; do
    name=$(basename "$f" .json)
    if [ -e "$d/$name.json" ] && ! cmp -s "$f" "$d/$name.json"; then
      say "  kept your changed $name (the shipped one is in $DEST/sounar/data/easyeffects)"
    else
      cp "$f" "$d/$name.json"
    fi
  done
  say "Easy Effects presets: $d"
}

install_services() {
  mkdir -p "$CONF/systemd/user"
  for u in $UNITS; do cp "$SRC/sounar/data/systemd/$u" "$CONF/systemd/user/$u"; done
  have systemctl && systemctl --user daemon-reload
  say "Services installed (setup turns them on): $UNITS"
}

install_omarchy() {
  have omarchy || return 0
  p="$CONF/omarchy/plugins/sounar.panel"
  rm -rf "$p"
  mkdir -p "$(dirname "$p")"
  cp -R "$SRC/integrations/omarchy/sounar.panel" "$p"
  if ask "Add the Sounar icon to the Omarchy bar?"; then
    omarchy plugin enable sounar.panel >/dev/null 2>&1 || true
  else
    say "Later: omarchy plugin enable sounar.panel"
  fi
  "$BIN" menu-install >/dev/null 2>&1 || true
  b="$CONF/hypr/bindings.lua"
  if [ ! -f "$b" ] || ! grep -q 'o.bind(' "$b"; then suggest_keys; return 0; fi
  grep -q -- "-- sounar:begin" "$b" && return 0        # added before: leave it as it is
  if grep -qE '"SUPER \+ ALT \+ (SHIFT \+ )?R"|"SUPER \+ ALT \+ Y"' "$b"; then
    say "SUPER+ALT+R / SUPER+ALT+SHIFT+R / SUPER+ALT+Y are already bound in $b; not adding Sounar's keys."
    suggest_keys
  elif ask "Add key bindings (SUPER+ALT+R menu, SUPER+ALT+SHIFT+R back, SUPER+ALT+Y sync)?"; then
    cat >> "$b" <<'EOF'
-- sounar:begin (written by Sounar's install.sh; `sounar uninstall` removes it)
o.bind("SUPER + ALT + R", "Sounar presets", "sounar menu")
o.bind("SUPER + ALT + SHIFT + R", "Sounar: previous preset", "sounar back")
o.bind("SUPER + ALT + Y", "Sounar: sync speakers", "sounar sync")
-- sounar:end
EOF
    say "Key bindings added to $b"
  fi
}

suggest_keys() {
  say "Suggested key bindings: \`sounar menu\` (presets), \`sounar back\` (previous preset), \`sounar sync\`."
}

install() {
  check
  install_program
  install_effects
  install_services
  install_omarchy
  have omarchy || suggest_keys
  say ""
  say "Next: sounar setup   (finds your speakers and asks where each one sits; nothing changes before you confirm)"
  case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) say "Add ~/.local/bin to your PATH to run \`sounar\`." ;; esac
}

uninstall() {
  purge=$1
  if have systemctl; then
    # shellcheck disable=SC2086
    systemctl --user disable --now $UNITS >/dev/null 2>&1
  fi
  for u in $UNITS; do rm -f "$CONF/systemd/user/$u"; done
  have systemctl && systemctl --user daemon-reload
  prev="$STATE/sounar/previous-default"
  if [ -f "$prev" ] && have pactl; then pactl set-default-sink "$(cat "$prev")" >/dev/null 2>&1; fi
  restart=0
  for f in "$CONF/pipewire/pipewire.conf.d/sounar.conf" "$CONF/wireplumber/wireplumber.conf.d/sounar-correction.conf" \
           "$CONF/wireplumber/wireplumber.conf.d/sounar-bluetooth.conf"; do
    [ -e "$f" ] && { rm -f "$f"; restart=1; }
  done
  if [ -d "$CONF/omarchy/plugins/sounar.panel" ]; then
    have omarchy && omarchy plugin disable sounar.panel >/dev/null 2>&1
    rm -rf "$CONF/omarchy/plugins/sounar.panel"
  fi
  strip_block "$CONF/omarchy/extensions/omarchy-menu.jsonc" "\/\/"
  strip_block "$CONF/hypr/bindings.lua" "--"
  d=$(ee_dir)
  if [ -n "$d" ]; then
    for f in "$SRC"/sounar/data/easyeffects/*.json; do
      t="$d/$(basename "$f")"
      [ -e "$t" ] && cmp -s "$f" "$t" && rm -f "$t"
    done
  fi
  grep -q "$MARK" "$BIN" 2>/dev/null && rm -f "$BIN"
  rm -rf "$DEST" "$CACHE/sounar"
  if [ "$purge" = 1 ]; then
    rm -rf "$CONF/sounar" "$STATE/sounar"
    say "Removed Sounar, your setup, presets and calibration."
  else
    say "Removed Sounar. Your setup and presets are still in $CONF/sounar (--purge removes them)."
  fi
  [ "$restart" = 1 ] && say "The \"Sounar\" output disappears when PipeWire restarts: systemctl --user restart pipewire pipewire-pulse wireplumber"
  return 0
}

main() {
  mode=install purge=0
  for a in "$@"; do
    case "$a" in
      --uninstall) mode=uninstall ;;
      --purge) purge=1 ;;
      --yes|-y) YES=1 ;;
      -h|--help) sed -n '2,8p' "$0"; exit 0 ;;
      *) say "unknown option: $a"; exit 2 ;;
    esac
  done
  if [ "$mode" = uninstall ]; then uninstall "$purge"; else install; fi
}

# Everything runs from main, which the shell has read in full before it starts: uninstalling
# may delete this very file.
main "$@"
exit $?
