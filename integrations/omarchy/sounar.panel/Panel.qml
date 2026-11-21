import QtQuick
import QtQuick.Controls
import Quickshell
import Quickshell.Io
import qs.Ui
import qs.Commons

// Sounar (https://github.com/zane8n/sounar): speaker presets for layout, balance, delay and
// Easy Effects. All the work happens in the `sounar` command; this panel only reads
// `sounar status --json` (docs/status-json.md) and runs the same commands the keys and menus
// use, so everything shares one state.
Panel {
  id: root
  moduleName: "sounar.panel"
  ipcTarget: "sounar.panel"
  manageIpc: false

  // omarchy-shell sounar.panel toggle | open | close | tab <presets|tune|speakers>
  IpcHandler {
    target: "sounar.panel"
    function open(): void { root.open() }
    function close(): void { root.close() }
    function show(): void { root.open() }
    function hide(): void { root.close() }
    function toggle(): void { root.toggle() }
    function tab(name: string): void {
      if (root.tabs.indexOf(name) >= 0) root.tab = name
      root.open()
    }
  }

  readonly property string home: Quickshell.env("HOME")
  readonly property string bin: home + "/.local/bin/sounar"
  readonly property string stateHome: Quickshell.env("XDG_STATE_HOME") || (home + "/.local/state")
  readonly property string currentFile: stateHome + "/sounar/current"

  function glyph(cp) { return String.fromCodePoint(cp) }
  readonly property string iconSpeaker: glyph(0xF04C3)
  readonly property string iconSpeakerOff: glyph(0xF04C4)
  readonly property string iconSpeakers: glyph(0xF0D38)
  readonly property string iconGrid: glyph(0xF0570)
  readonly property string iconTune: glyph(0xF062E)
  readonly property string iconBack: glyph(0xF054C)
  readonly property string iconReset: glyph(0xF099B)
  readonly property string iconSync: glyph(0xF04E6)
  readonly property string iconTarget: glyph(0xF04FE)
  readonly property string iconAlert: glyph(0xF0026)
  readonly property string iconEdit: glyph(0xF03EB)
  readonly property string iconDoc: glyph(0xF0219)
  readonly property string iconChevronUp: glyph(0xF0143)
  readonly property string iconChevronDown: glyph(0xF0140)
  readonly property string iconMinus: glyph(0xF0374)
  readonly property string iconPlus: glyph(0xF0415)
  readonly property string iconRoom: glyph(0xF0821)
  readonly property string iconUndo: glyph(0xF054D)
  readonly property string iconPin: glyph(0xF0403)

  // ---------- State from `sounar status --json` ----------
  property var st: null
  readonly property bool ready: st !== null
  readonly property bool setupDone: ready && st.setup.done
  readonly property var outputs: ready ? st.outputs : []
  readonly property int total: outputs.length
  readonly property int connected: outputs.filter(function(o) { return o.connected }).length
  readonly property bool allConnected: total > 0 && connected === total
  readonly property bool hasEffects: ready && st.effects.available
  readonly property string missingName: {
    for (var i = 0; i < outputs.length; i++) if (!outputs[i].connected) return shortLabel(outputs[i])
    return ""
  }
  // An output by what it holds: "Front left", "Right speakers", or a nickname.
  function shortLabel(o) {
    if (!o) return ""
    var s = o.speakers
    if (s.length === 1) return s[0].nickname || s[0].name
    var sides = s.map(function(x) { return x.name.split(" ").pop() })
    if (sides.every(function(x) { return x === "right" })) return "Right speakers"
    if (sides.every(function(x) { return x === "left" })) return "Left speakers"
    return s[0].name + " +" + (s.length - 1)
  }
  function hourText(x) { return x.zone === "sub" ? "sub" : (Math.round(x.at * 10) / 10) + " o'clock" }

  // Optimistic UI while sounar applies a change.
  property string pendingPreset: ""
  property var optimistic: ({})
  property string starting: ""           // "sync" / "calibrate" until the status shows it running
  property string error: ""

  readonly property string activeId: pendingPreset !== "" ? pendingPreset : (ready ? st.preset : "")
  readonly property var activePreset: presetById(activeId)
  readonly property string measuringNow: ready && st.measuring ? st.measuring : starting
  readonly property bool busyMeasuring: measuringNow !== ""

  // ---------- sync back (stable / undo) and the room: map, walls, measurement ----------
  readonly property var syncInfo: ready && st.sync ? st.sync : ({ undo: null, stable: null })
  readonly property var roomInfo: ready && st.room ? st.room : null
  property var wallOverride: null       // {left, right, ahead, behind} while dragged / on its way
  property string dragWall: ""
  property string dragSpeaker: ""
  property var speakerOverride: ({})    // speaker id -> angle while dragged / on its way
  readonly property var wallNames: ({ left: "Left wall", right: "Right wall", ahead: "Wall ahead", behind: "Wall behind" })
  function angleOf(at) { var a = (at % 12) * 30; return a > 180 ? a - 360 : a }
  // Every speaker on the map: its direction (from setup.toml's hour), and how far the room
  // measurement heard it (1.5 m when it wasn't measured: the map then shows directions only).
  readonly property var mapSpeakers: {
    var out = []
    for (var i = 0; i < outputs.length; i++)
      for (var j = 0; j < outputs[i].speakers.length; j++) {
        var sp = outputs[i].speakers[j]
        if (sp.at === null || sp.zone === "sub") continue
        var est = roomInfo && roomInfo.speakers && roomInfo.speakers[sp.id] ? roomInfo.speakers[sp.id].dist_est : null
        var ang = speakerOverride[sp.id] !== undefined ? speakerOverride[sp.id] : angleOf(sp.at)
        out.push({ id: sp.id, label: sp.nickname || sp.name, angle: ang, dist: est || 1.5, measured: !!est })
      }
    return out
  }
  function boxWalls() {
    var b = roomInfo ? roomInfo.box : null
    if (!b || !b.laptop) return null
    return { left: b.laptop.x, right: b.width - b.laptop.x, ahead: b.laptop.y, behind: b.length - b.laptop.y }
  }
  function walls() { return wallOverride || boxWalls() }
  function moveWall(side, d) {
    var w = Object.assign({}, walls())
    w[side] = Math.max(0.2, Math.min(8, Math.round(d * 20) / 20))
    wallOverride = w
  }
  function dropWall() {
    var w = walls()
    if (!w) return
    settleTimer.restart()
    run(["room", "walls", w.left.toFixed(2), w.right.toFixed(2), w.ahead.toFixed(2), w.behind.toFixed(2)])
  }
  function moveSpeaker(id, angle) {
    var o = Object.assign({}, speakerOverride)
    o[id] = Math.round(angle / 15) * 15
    speakerOverride = o
  }
  function dropSpeaker(id) {
    var a = speakerOverride[id]
    if (a === undefined) return
    var hour = ((a / 30) % 12 + 12) % 12
    settleTimer.restart()
    run(["speaker", id, "at", String(hour === 0 ? 12 : Math.round(hour * 100) / 100)])
  }
  Timer { id: settleTimer; interval: 5000; onTriggered: { root.wallOverride = null; root.speakerOverride = ({}) } }
  onRoomInfoChanged: {
    if (dragWall !== "" || !wallOverride) return
    var b = boxWalls()
    if (b && ["left", "right", "ahead", "behind"].every(function(k) { return Math.abs(b[k] - wallOverride[k]) < 0.02 }))
      wallOverride = null
  }
  readonly property string roomCaption: {
    if (dragWall !== "") {
      var w = walls()
      return wallNames[dragWall] + ": " + w[dragWall].toFixed(2) + " m from you · room "
        + (w.left + w.right).toFixed(1) + " × " + (w.ahead + w.behind).toFixed(1) + " m"
    }
    if (dragSpeaker !== "") {
      var sp = mapSpeakers.filter(function(x) { return x.id === dragSpeaker })[0]
      return sp ? sp.label + " · " + (Math.abs(sp.angle) < 1 ? "ahead" : Math.abs(sp.angle) > 179 ? "behind"
        : Math.round(Math.abs(sp.angle)) + "° " + (sp.angle > 0 ? "right" : "left")) : ""
    }
    return "Drag a speaker around you to where it stands (the upmixer aims sounds by these directions), "
      + "and the wall grips to where the walls are."
  }
  function roomSummary() {
    var r = roomInfo
    if (!r) return "Not measured yet. Measuring plays about 30 s of sweeps (the computer's speaker, then each speaker), "
      + "music paused: it hears the room's echo and size, roughly how far each speaker is, and the bass booms at your seat."
    var b = r.box, when = Qt.formatDateTime(new Date(r.ts), "d MMM, HH:mm")
    var cuts = r.cuts.length ? r.cuts.map(function(c) { return Math.round(c.freq) + " Hz " + String(c.gain.toFixed(1)).replace("-", "−") + " dB" }).join(", ")
      : "no booms worth cutting"
    return "Measured " + when + ": echo " + r.rt60.toFixed(2) + " s · room about " + b.width.toFixed(1) + " × "
      + b.length.toFixed(1) + " m" + (r.walls_by_user ? " (your walls)" : b.known && b.known.width && b.known.length ? "" : " (partly guessed)")
      + " · bass cuts " + cuts + (r.correction ? "" : " (off)") + ". Distances are rough: the dots sit where the measurement heard them."
  }
  function measureRoom() {
    if (busyMeasuring || !allConnected) return
    starting = "room"
    startingTimeout.restart()
    Quickshell.execDetached([root.bin, "room", "measure"])
    refreshSoon.restart()
  }
  readonly property bool tweaked: ready && st.tweaked && pendingPreset === ""

  // Stable models: replaced only when their contents change, so a status refresh never
  // rebuilds a slider under the mouse.
  property var roomIds: []
  property var otherIds: []
  property var paramKeys: []
  property var eeNames: []
  readonly property var allParams: ready && pendingPreset === "" ? st.params : []
  readonly property var advancedKeys: allParams.filter(function(p) { return p.advanced }).map(function(p) { return p.key })
  readonly property var visibleKeys: paramKeys.filter(function(k) { return showMore || advancedKeys.indexOf(k) < 0 })

  property string tab: "presets"
  readonly property var tabs: ["presets", "tune", "speakers", "room"]
  property bool showMore: false
  property string hoverHint: ""
  property string hoverPreset: ""

  function presetById(id) {
    if (!ready) return null
    for (var i = 0; i < st.presets.length; i++) if (st.presets[i].id === id) return st.presets[i]
    return null
  }
  function presetIndex(id) {
    if (!ready) return -1
    for (var i = 0; i < st.presets.length; i++) if (st.presets[i].id === id) return i
    return -1
  }
  function paramByKey(k) {
    for (var i = 0; i < allParams.length; i++) if (allParams[i].key === k) return allParams[i]
    return null
  }
  function layoutName(l) {
    return l === "room" ? "Room layout" : (l === "spread" ? "Spread layout" : "Stereo, as wired")
  }

  readonly property string heroStatus: {
    if (!ready) return "Loading"
    if (!setupDone) return "Setup needed"
    if (measuringNow === "sync") return "Syncing speakers · music paused"
    if (measuringNow === "calibrate") return "Calibrating · music paused"
    if (measuringNow === "room") return "Measuring the room · music paused"
    if (connected === 0) return "No speakers connected"
    if (connected < total) return missingName + " missing · plain stereo"
    if (pendingPreset !== "") return "Switching"
    if (!st.applied) return "Re-applying the preset"
    var bits = [st.live_layout === "stereo" ? "as wired" : st.live_layout]
    var e = st.effects
    if (e.available && e.bypass) bits.push("effects off")
    else if (e.live) bits.push(e.live)
    if (e.leveling && e.target_live !== null && !e.bypass) bits.push(minus(e.target_live) + " LUFS")
    return bits.join(" · ")
  }
  readonly property bool heroWarn: ready && !busyMeasuring
    && (!setupDone || connected < total || (!st.applied && pendingPreset === ""))

  // ---------- Formatting ----------
  function minus(s) { return String(s).replace("-", "−") }
  function fmt(p, v) {
    if (!p) return ""
    if (p.key === "width") return Math.round(v * 100) + "%"
    if (p.unit === "Hz") return v >= 1000 ? (v / 1000).toFixed(1) + " kHz" : Math.round(v) + " Hz"
    if (p.unit === "ms") return Math.round(v) + " ms"
    var s = v.toFixed(1)
    if ((p.key === "vocal" || p.key === "bass") && v > 0) s = "+" + s
    return minus(s) + " dB"
  }
  // Two outputs get one balance row and one sync row; more get one row per output.
  readonly property bool pair: total === 2
  function balanceOf(o) {
    var k = "bal:" + o.id
    return optimistic[k] !== undefined ? optimistic[k] : o.balance_db
  }
  function dbText(v) { return (v > 0 ? "+" : "") + minus(v.toFixed(1)) + " dB" }
  readonly property string pairBalanceText: {
    if (!pair) return ""
    var d = balanceOf(outputs[1]) - balanceOf(outputs[0])
    if (Math.abs(d) < 0.05) return "Even"
    return shortLabel(d > 0 ? outputs[1] : outputs[0]) + " +" + Math.abs(d).toFixed(1) + " dB"
  }
  function syncOf(o) {
    var k = "sync:" + o.id
    return optimistic[k] !== undefined ? optimistic[k] : o.sync_ms
  }
  function syncText(list) {
    var w = list.filter(function(o) { return syncOf(o) >= 0.05 })
    if (w.length === 0) return "No delay"
    return w.map(function(o) { return shortLabel(o) + " waits " + syncOf(o).toFixed(1) + " ms" }).join(" · ")
  }
  readonly property string calibrationText: {
    if (!ready) return ""
    var c = st.calibration
    if (!c.measured) return "Not calibrated yet: Calibrate balances the speakers and lines them up for your seat."
    return "Calibrated " + Qt.formatDateTime(new Date(c.ts), "d MMM, HH:mm") + ". Calibrate again after moving a speaker."
  }

  // ---------- Commands: one at a time, in order ----------
  property var queue: []
  property int changeSeq: 0
  property int statusSeq: -1
  property int lastExit: 0

  function runRaw(cmd) {
    changeSeq++
    error = ""
    queue = queue.concat([cmd])
    pump()
  }
  function run(args) { runRaw([root.bin, "-q"].concat(args)) }
  function pump() {
    if (runner.running || queue.length === 0) return
    runner.command = queue[0]
    queue = queue.slice(1)
    runner.running = true
  }
  function finishRun() {
    if (lastExit !== 0) {
      var msg = (runOut.text + "\n" + runErr.text).trim().split("\n").filter(function(l) { return l !== "" })
      error = msg.length ? msg[msg.length - 1].replace(/^sounar: /, "") : "That didn't work"
      errorClear.restart()
      optimistic = ({})
      pendingPreset = ""
    }
    if (queue.length > 0) pump()
    else refreshSoon.restart()
  }

  function setPreset(id) {
    if (!ready || busyMeasuring || id === activeId) return
    pendingPreset = id
    optimistic = ({})
    hoverHint = ""
    run(["set", id])
  }
  function back() {
    if (!ready || !st.previous) return
    if (opened) setPreset(st.previous)
    else { Quickshell.execDetached([root.bin, "back"]); refreshSoon.restart() }
  }
  function resetTweaks() {
    if (!tweaked) return
    optimistic = ({})
    run(["reset"])
  }

  function paramValue(p) {
    if (!p) return 0
    return optimistic[p.key] !== undefined ? optimistic[p.key] : p.value
  }
  function snap(p, v) {
    var r = Math.round((v - p.min) / p.step) * p.step + p.min
    return Math.round(Math.max(p.min, Math.min(p.max, r)) * 100) / 100
  }
  property var pendingTune: ({})
  function setParam(p, v) {
    if (!p || busyMeasuring) return
    v = snap(p, v)
    if (Math.abs(v - paramValue(p)) < 1e-6) return
    var o = Object.assign({}, optimistic); o[p.key] = v; optimistic = o
    var t = Object.assign({}, pendingTune); t[p.key] = v; pendingTune = t
    changeSeq++
    tuneDebounce.restart()
  }
  function flushTune() {
    var args = []
    for (var k in pendingTune) args.push(k + "=" + pendingTune[k])
    pendingTune = ({})
    if (args.length) run(["tune"].concat(args))
  }

  function setEe(name) {
    if (!ready || name === st.effects.preset) return
    var e = Object.assign({}, st.effects, { preset: name, live: name })
    st = Object.assign({}, st, { effects: e })
    run(["effects", name])
  }
  function toggleLeveling() {
    if (!ready) return
    var on = optimistic.leveling !== undefined ? optimistic.leveling : st.effects.leveling
    optimistic = Object.assign({}, optimistic, { leveling: !on })
    run(["effects", "leveling", on ? "off" : "on"])
  }
  function toggleEffects() {
    if (!ready) return
    var off = optimistic.bypass !== undefined ? optimistic.bypass : st.effects.bypass
    optimistic = Object.assign({}, optimistic, { bypass: !off })
    run(["effects", off ? "on" : "off"])
  }
  readonly property bool levelingOn: optimistic.leveling !== undefined ? optimistic.leveling : (ready && st.effects.leveling)
  readonly property bool effectsOn: !(optimistic.bypass !== undefined ? optimistic.bypass : (ready && st.effects.bypass))

  function outputById(id) {
    for (var i = 0; i < outputs.length; i++) if (outputs[i].id === id) return outputs[i]
    return null
  }
  // One output louder (+) or quieter (-); the others move the other way, as `sounar balance` does.
  function balance(id, d) {
    var o = outputById(id)
    if (!o || busyMeasuring || !allConnected || total < 2) return
    var opt = Object.assign({}, optimistic)
    opt["bal:" + id] = balanceOf(o) + d * (total - 1) / total
    outputs.forEach(function(x) { if (x.id !== id) opt["bal:" + x.id] = balanceOf(x) - d / total })
    optimistic = opt
    run(["balance", id, (d > 0 ? "+" : "") + d])
  }
  // One output later (+) or sooner (-); delays only exist, so the smallest stays at 0.
  function nudge(id, ms) {
    var o = outputById(id)
    if (!o || busyMeasuring) return
    var want = {}
    outputs.forEach(function(x) { want[x.id] = syncOf(x) + (x.id === id ? ms : 0) })
    var lo = Math.min.apply(null, outputs.map(function(x) { return want[x.id] }))
    var opt = Object.assign({}, optimistic)
    outputs.forEach(function(x) { opt["sync:" + x.id] = Math.round((want[x.id] - lo) * 10) / 10 })
    optimistic = opt
    run(["nudge", id, (ms > 0 ? "+" : "") + ms])
  }
  function measure(kind) {
    if (busyMeasuring || !allConnected || (kind === "sync" && total < 2)) return
    starting = kind
    startingTimeout.restart()
    Quickshell.execDetached([root.bin, kind])      // not quiet: it reports its result itself
    refreshSoon.restart()
  }
  function openFile(which) {
    if (!ready) return
    Quickshell.execDetached(["omarchy-launch-editor", st.files[which]])
    close()
  }
  function runSetup() {
    Quickshell.execDetached(["xdg-terminal-exec", root.bin, "setup"])
    close()
  }

  // ---------- Status ----------
  function refresh() {
    if (statusProc.running) return
    statusSeq = changeSeq
    statusProc.running = true
  }
  function sameList(a, b) { return JSON.stringify(a) === JSON.stringify(b) }
  function applyStatus(text) {
    if (statusSeq !== changeSeq || runner.running || queue.length > 0 || tuneDebounce.running) return
    var next
    try { next = JSON.parse(text) } catch (e) { return }
    st = next
    pendingPreset = ""
    optimistic = ({})
    if (next.measuring) starting = ""
    var room = [], other = []
    for (var i = 0; i < next.presets.length; i++)
      (next.presets[i].layout === "room" ? room : other).push(next.presets[i].id)
    if (!sameList(room, roomIds)) roomIds = room
    if (!sameList(other, otherIds)) otherIds = other
    var keys = next.params.map(function(p) { return p.key })
    if (!sameList(keys, paramKeys)) paramKeys = keys
    if (!sameList(next.effects.presets, eeNames)) eeNames = next.effects.presets
  }

  Process {
    id: statusProc
    command: [root.bin, "status", "--json"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.applyStatus(text)
    }
  }

  Process {
    id: runner
    stdout: StdioCollector { id: runOut; waitForEnd: true }
    stderr: StdioCollector { id: runErr; waitForEnd: true }
    onExited: function(code) { root.lastExit = code; afterRun.restart() }
  }

  // The preset file changes on every switch, from the keys and the menu too.
  FileView {
    path: root.currentFile
    watchChanges: true
    printErrors: false
    onFileChanged: refreshSoon.restart()
  }

  Timer {
    interval: root.busyMeasuring ? 2000 : (root.opened ? 4000 : 60000)
    running: true
    repeat: true
    triggeredOnStart: true
    onTriggered: root.refresh()
  }
  Timer { id: refreshSoon; interval: 300; onTriggered: root.refresh() }
  Timer { id: afterRun; interval: 60; onTriggered: root.finishRun() }
  Timer { id: tuneDebounce; interval: 220; onTriggered: root.flushTune() }
  Timer { id: errorClear; interval: 9000; onTriggered: root.error = "" }
  Timer { id: startingTimeout; interval: 8000; onTriggered: root.starting = "" }

  // ---------- Keyboard cursor: row -1 = tabs, then the rows of the open tab ----------
  property int row: -1
  property int col: 0
  property bool cursorActive: false

  function chunk(a, n) {
    var out = []
    for (var i = 0; i < a.length; i += n) out.push(a.slice(i, i + n))
    return out
  }
  readonly property var rows: {
    var out = []
    if (!ready) return out
    if (!setupDone) {
      out.push({ type: "setup", keys: ["setup"] })
      return out
    }
    if (tab === "presets") {
      chunk(roomIds, 3).concat(chunk(otherIds, 3)).forEach(function(c) { out.push({ type: "preset", keys: c }) })
    } else if (tab === "tune") {
      visibleKeys.forEach(function(k) { out.push({ type: "param", keys: [k] }) })
      if (advancedKeys.length > 0) out.push({ type: "more", keys: ["more"] })
      if (hasEffects) {
        chunk(eeNames, 3).forEach(function(c) { out.push({ type: "ee", keys: c }) })
        out.push({ type: "toggle", keys: ["leveling"] })
        out.push({ type: "toggle", keys: ["effects"] })
      }
    } else if (tab === "speakers") {
      if (pair) {
        out.push({ type: "balance", keys: ["+:" + outputs[0].id, "+:" + outputs[1].id] })
        out.push({ type: "nudge", keys: ["-:" + outputs[0].id, "+:" + outputs[0].id] })
      } else if (total > 2) {
        outputs.forEach(function(o) { out.push({ type: "balance", keys: ["-:" + o.id, "+:" + o.id] }) })
        outputs.slice(1).forEach(function(o) { out.push({ type: "nudge", keys: ["-:" + o.id, "+:" + o.id] }) })
      }
      out.push({ type: "measure", keys: ["sync", "calibrate"] })
      out.push({ type: "file", keys: ["presets", "setup", "docs"] })
    }
    return out
  }
  onRowsChanged: if (row >= rows.length) row = rows.length - 1

  function isCursor(type, key) {
    if (!cursorActive || row < 0 || row >= rows.length) return false
    return rows[row].type === type && rows[row].keys[col] === key
  }
  function cursorTo(type, key) {
    for (var r = 0; r < rows.length; r++) {
      if (rows[r].type !== type) continue
      var c = rows[r].keys.indexOf(key)
      if (c >= 0) { row = r; col = c; cursorActive = true; return }
    }
  }
  function switchTab(d) {
    var i = (tabs.indexOf(tab) + d + tabs.length) % tabs.length
    tab = tabs[i]
  }
  function moveCursor(dx, dy) {
    if (!cursorActive) { cursorActive = true; return }
    if (dy !== 0) {
      row = Math.max(-1, Math.min(rows.length - 1, row + dy))
      if (row >= 0) col = Math.min(col, rows[row].keys.length - 1)
      return
    }
    if (row < 0) { switchTab(dx); return }
    var r = rows[row]
    if (r.type === "param") {
      var p = paramByKey(r.keys[0])
      if (p) setParam(p, paramValue(p) + dx * p.step)
      return
    }
    col = Math.max(0, Math.min(r.keys.length - 1, col + dx))
  }
  function activateCursor() {
    if (!cursorActive || row < 0 || row >= rows.length) return
    var r = rows[row], k = r.keys[col]
    if (r.type === "preset") setPreset(k)
    else if (r.type === "param") { var p = paramByKey(k); if (p) setParam(p, p.preset) }
    else if (r.type === "more") showMore = !showMore
    else if (r.type === "ee") setEe(k)
    else if (r.type === "toggle") { if (k === "leveling") toggleLeveling(); else toggleEffects() }
    else if (r.type === "balance") balance(k.slice(2), k[0] === "+" ? 0.5 : -0.5)
    else if (r.type === "nudge") nudge(k.slice(2), k[0] === "+" ? 1 : -1)
    else if (r.type === "setup") runSetup()
    else if (r.type === "measure") measure(k)
    else if (r.type === "file") openFile(k)
  }

  onOpenedChanged: {
    if (!opened) return
    refresh()
    cursorActive = false
    row = -1
    col = 0
    hoverHint = ""
    hoverPreset = ""
  }
  onActiveIdChanged: if (opened) heroPop.restart()

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: root.activePreset ? root.activePreset.icon : root.iconSpeaker
    active: root.busyMeasuring
    useActiveColor: true
    activeColor: Color.accent
    dimmed: root.ready && (!root.setupDone || root.connected === 0)
    tooltipText: {
      if (!root.ready) return "Sounar"
      var lines = ["Sounar · " + (root.activePreset ? root.activePreset.label : root.activeId)]
      if (root.busyMeasuring) lines.push(root.heroStatus)
      else if (root.heroWarn) lines.push(root.heroStatus)
      else if (root.activePreset) lines.push(root.activePreset.tagline)
      var prev = root.presetById(root.st.previous)
      lines.push("Right-click: back to " + (prev ? prev.label : "the previous preset"))
      return lines.join("\n")
    }
    onPressed: function(b) {
      if (b === Qt.RightButton) root.back()
      else root.toggle()
    }

    SequentialAnimation on opacity {
      running: root.busyMeasuring
      loops: Animation.Infinite
      alwaysRunToEnd: true
      NumberAnimation { from: 1.0; to: 0.5; duration: 900; easing.type: Easing.InOutSine }
      NumberAnimation { from: 0.5; to: 1.0; duration: 900; easing.type: Easing.InOutSine }
    }
  }

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(400))
    contentHeight: panel.fittedContentHeight(column.implicitHeight)

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onMoveRequested: function(dx, dy) { root.moveCursor(dx, dy) }
      onActivateRequested: root.activateCursor()
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onTextKey: function(t) {
        if (t >= "1" && t <= "9") {
          var i = parseInt(t) - 1
          if (root.ready && i < root.st.presets.length) root.setPreset(root.st.presets[i].id)
        } else if (t === "r" || t === "R") root.resetTweaks()
        else if (t === "b" || t === "B") root.back()
        else if (t === "[") root.switchTab(-1)
        else if (t === "]") root.switchTab(1)
      }

      Column {
        id: column
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        spacing: Style.space(12)

        // ---------- Hero: the active preset ----------
        Item {
          width: parent.width
          implicitHeight: Math.max(heroIcon.implicitHeight, heroLabels.implicitHeight, backBtn.implicitHeight)

          Text {
            id: heroIcon
            textFormat: Text.PlainText
            anchors.left: parent.left
            anchors.verticalCenter: parent.verticalCenter
            width: Style.space(44)
            horizontalAlignment: Text.AlignHCenter
            text: root.setupDone && root.activePreset ? root.activePreset.icon : root.iconSpeaker
            color: root.heroWarn ? Color.urgent : Color.accent
            font.family: root.bar.fontFamily
            font.pixelSize: Style.font.display
            Behavior on color { ColorAnimation { duration: 220 } }

            SequentialAnimation {
              id: heroPop
              NumberAnimation { target: heroIcon; property: "scale"; to: 1.2; duration: 110; easing.type: Easing.OutCubic }
              NumberAnimation { target: heroIcon; property: "scale"; to: 1.0; duration: 280; easing.type: Easing.OutBack }
            }
            SequentialAnimation on opacity {
              running: root.busyMeasuring && root.opened
              loops: Animation.Infinite
              alwaysRunToEnd: true
              NumberAnimation { from: 1; to: 0.45; duration: 800; easing.type: Easing.InOutSine }
              NumberAnimation { from: 0.45; to: 1; duration: 800; easing.type: Easing.InOutSine }
            }
          }

          Column {
            id: heroLabels
            anchors.left: heroIcon.right
            anchors.leftMargin: Style.space(12)
            anchors.right: backBtn.visible ? backBtn.left : parent.right
            anchors.rightMargin: backBtn.visible ? Style.space(10) : 0
            anchors.verticalCenter: parent.verticalCenter
            spacing: Style.space(2)

            Row {
              width: parent.width
              spacing: Style.space(8)
              Text {
                id: heroTitle
                textFormat: Text.PlainText
                text: root.setupDone && root.activePreset ? root.activePreset.label : "Sounar"
                color: root.bar.foreground
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.title
                font.bold: true
                elide: Text.ElideRight
                width: Math.min(implicitWidth, parent.width - (tunedPill.visible ? tunedPill.width + parent.spacing : 0))
              }
              Rectangle {
                id: tunedPill
                visible: root.tweaked
                anchors.verticalCenter: heroTitle.verticalCenter
                width: tunedText.implicitWidth + Style.space(12)
                height: tunedText.implicitHeight + Style.space(4)
                radius: height / 2
                color: Util.alpha(Color.accent, 0.16)
                border.width: 1
                border.color: Util.alpha(Color.accent, 0.6)
                Text {
                  id: tunedText
                  anchors.centerIn: parent
                  textFormat: Text.PlainText
                  text: "TUNED"
                  color: Color.accent
                  font.family: root.bar.fontFamily
                  font.pixelSize: Style.font.caption
                  font.bold: true
                  font.letterSpacing: 1
                }
              }
            }
            Text {
              textFormat: Text.PlainText
              text: (root.heroWarn ? root.iconAlert + " " : "") + root.heroStatus.toUpperCase()
              color: root.heroWarn ? Color.urgent : (root.busyMeasuring ? Color.accent : Qt.darker(root.bar.foreground, 1.4))
              font.family: root.bar.fontFamily
              font.pixelSize: Style.font.caption
              font.bold: true
              font.letterSpacing: 1.2
              elide: Text.ElideRight
              width: parent.width
            }
          }

          Button {
            id: backBtn
            visible: root.ready && !!root.st.previous && root.st.previous !== root.activeId
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            iconText: root.iconBack
            text: root.presetById(root.ready ? root.st.previous : "") ? root.presetById(root.st.previous).label : ""
            fontSize: Style.font.caption
            foreground: root.bar.foreground
            fontFamily: root.bar.fontFamily
            bordered: true
            enabled: !root.busyMeasuring
            opacity: enabled ? 1 : 0.45
            tooltipText: "Back to the preset before (B, or right-click the bar icon)"
            onClicked: root.back()
          }
        }

        // Measuring: an indeterminate bar under the hero.
        Rectangle {
          id: progress
          visible: root.busyMeasuring
          width: parent.width
          height: Style.space(3)
          radius: height / 2
          color: Util.alpha(Color.accent, 0.18)
          clip: true
          Rectangle {
            width: progress.width * 0.3
            height: parent.height
            radius: height / 2
            color: Color.accent
            NumberAnimation on x {
              running: progress.visible && root.opened
              loops: Animation.Infinite
              from: -progress.width * 0.3
              to: progress.width
              duration: 1400
              easing.type: Easing.InOutSine
            }
          }
        }

        Text {
          visible: root.error !== ""
          width: parent.width
          wrapMode: Text.WordWrap
          textFormat: Text.PlainText
          text: root.iconAlert + "  " + root.error
          color: Color.urgent
          font.family: root.bar.fontFamily
          font.pixelSize: Style.font.bodySmall
        }

        // ---------- Tabs ----------
        Row {
          id: tabRow
          visible: root.setupDone
          width: parent.width
          spacing: Style.space(6)
          readonly property real cellWidth: (width - spacing * (root.tabs.length - 1)) / root.tabs.length

          Repeater {
            model: [
              { value: "presets", label: "Presets", icon: root.iconGrid },
              { value: "tune", label: "Tune", icon: root.iconTune },
              { value: "speakers", label: "Speakers", icon: root.iconSpeakers },
              { value: "room", label: "Room", icon: root.iconRoom }
            ]
            Button {
              required property var modelData
              width: tabRow.cellWidth
              iconText: modelData.icon
              text: modelData.label
              fontSize: Style.font.bodySmall
              foreground: root.bar.foreground
              fontFamily: root.bar.fontFamily
              bordered: true
              selected: root.tab === modelData.value
              hasCursor: root.cursorActive && root.row === -1 && root.tab === modelData.value
              onClicked: root.tab = modelData.value
            }
          }
        }

        // ---------- The open tab (scrolls only if the screen is too short) ----------
        Flickable {
          id: body
          width: parent.width
          readonly property real maxHeight: panel.availableCardHeight > 0
            ? panel.availableCardHeight - panel.verticalContentInset - y : 100000
          height: Math.min(bodyColumn.implicitHeight, maxHeight)
          contentWidth: width
          contentHeight: bodyColumn.implicitHeight
          clip: true
          flickableDirection: Flickable.VerticalFlick
          boundsBehavior: Flickable.StopAtBounds
          interactive: contentHeight > height
          ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

          Column {
            id: bodyColumn
            width: body.width

            // ---------- No setup yet ----------
            Column {
              visible: root.ready && !root.setupDone
              width: parent.width
              spacing: Style.space(10)

              Text {
                width: parent.width
                wrapMode: Text.WordWrap
                textFormat: Text.PlainText
                text: "Tell Sounar about your speakers: it finds them, plays a soft tone on each so you know "
                  + "which is which, and asks where each one sits. Then a one-minute measurement lines them up "
                  + "for your seat."
                color: root.bar.foreground
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.bodySmall
              }
              Button {
                width: parent.width
                iconText: root.iconSpeakers
                text: "Set up speakers"
                fontSize: Style.font.bodySmall
                foreground: root.bar.foreground
                fontFamily: root.bar.fontFamily
                bordered: true
                hasCursor: root.isCursor("setup", "setup")
                tooltipText: "Opens `sounar setup` in a terminal"
                onClicked: root.runSetup()
                onHovered: function(h) { if (h) root.cursorTo("setup", "setup") }
              }
            }

            // ---------- Presets ----------
            Column {
              visible: root.setupDone && root.tab === "presets"
              width: parent.width
              spacing: Style.space(8)

              PanelSectionHeader {
                text: "AT YOUR SEAT · ROOM LAYOUT"
                foreground: root.bar.foreground
                fontFamily: root.bar.fontFamily
              }
              Grid {
                id: roomGrid
                width: parent.width
                columns: 3
                spacing: Style.space(6)
                readonly property real cellWidth: (width - spacing * 2) / 3
                Repeater {
                  model: root.roomIds
                  PresetTile {
                    required property var modelData
                    width: roomGrid.cellWidth
                    pid: modelData
                  }
                }
              }

              PanelSectionHeader {
                visible: root.otherIds.length > 0
                text: "ANYWHERE · STEREO OR SPREAD"
                foreground: root.bar.foreground
                fontFamily: root.bar.fontFamily
              }
              Grid {
                id: otherGrid
                width: parent.width
                columns: 3
                spacing: Style.space(6)
                readonly property real cellWidth: (width - spacing * 2) / 3
                Repeater {
                  model: root.otherIds
                  PresetTile {
                    required property var modelData
                    width: otherGrid.cellWidth
                    pid: modelData
                  }
                }
              }

              // What the hovered (or active) preset does.
              Column {
                readonly property var shown: root.presetById(root.hoverPreset !== "" ? root.hoverPreset : root.activeId)
                visible: !!shown
                width: parent.width
                spacing: Style.space(2)
                topPadding: Style.space(4)

                Text {
                  width: parent.width
                  textFormat: Text.PlainText
                  wrapMode: Text.WordWrap
                  text: parent.shown ? parent.shown.tagline : ""
                  color: root.bar.foreground
                  font.family: root.bar.fontFamily
                  font.pixelSize: Style.font.bodySmall
                }
                Text {
                  width: parent.width
                  textFormat: Text.PlainText
                  elide: Text.ElideRight
                  text: parent.shown ? root.layoutName(parent.shown.layout)
                    + (root.hasEffects && parent.shown.ee ? " · Easy Effects " + parent.shown.ee : "")
                    + (parent.shown.tweaked ? " · fine-tuned" : "") : ""
                  color: Qt.darker(root.bar.foreground, 1.5)
                  font.family: root.bar.fontFamily
                  font.pixelSize: Style.font.caption
                }
              }
            }

            // ---------- Tune: the active preset's values, Easy Effects ----------
            Column {
              visible: root.setupDone && root.tab === "tune"
              width: parent.width
              spacing: Style.space(8)

              Item {
                width: parent.width
                implicitHeight: Math.max(tuneHeader.implicitHeight, resetBtn.implicitHeight)
                PanelSectionHeader {
                  id: tuneHeader
                  anchors.left: parent.left
                  anchors.verticalCenter: parent.verticalCenter
                  text: "TUNE " + (root.activePreset ? root.activePreset.label.toUpperCase() : "")
                  foreground: root.bar.foreground
                  fontFamily: root.bar.fontFamily
                }
                Button {
                  id: resetBtn
                  visible: root.tweaked
                  anchors.right: parent.right
                  anchors.verticalCenter: parent.verticalCenter
                  iconText: root.iconReset
                  text: "Reset"
                  fontSize: Style.font.caption
                  foreground: root.bar.foreground
                  fontFamily: root.bar.fontFamily
                  bordered: true
                  tooltipText: "Back to this preset's own values (R)"
                  onClicked: root.resetTweaks()
                }
              }

              Text {
                visible: root.ready && root.paramKeys.length === 0
                width: parent.width
                wrapMode: Text.WordWrap
                textFormat: Text.PlainText
                text: "This preset plays every speaker as wired, so there's nothing to arrange. "
                  + "The speaker balance and sync are under Speakers."
                color: Qt.darker(root.bar.foreground, 1.35)
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.bodySmall
              }

              Repeater {
                model: root.visibleKeys
                ParamRow {
                  required property var modelData
                  width: parent.width
                  pkey: modelData
                }
              }

              Button {
                visible: root.advancedKeys.length > 0
                width: parent.width
                leftAlign: true
                iconText: root.showMore ? root.iconChevronUp : root.iconChevronDown
                text: root.showMore ? "Fewer settings" : "More settings (" + root.advancedKeys.length + ")"
                fontSize: Style.font.caption
                foreground: Qt.darker(root.bar.foreground, 1.3)
                fontFamily: root.bar.fontFamily
                hasCursor: root.isCursor("more", "more")
                onClicked: root.showMore = !root.showMore
                onHovered: function(h) { if (h) root.cursorTo("more", "more") }
              }

              Text {
                visible: root.paramKeys.length > 0
                width: parent.width
                wrapMode: Text.WordWrap
                textFormat: Text.PlainText
                text: root.hoverHint !== "" ? root.hoverHint
                  : "Plays right away, saved for this preset. The notch is the preset's own value: right-click a slider (or Enter) to go back to it."
                color: Qt.darker(root.bar.foreground, 1.45)
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.caption
              }

              PanelSeparator { foreground: root.bar.foreground }

              PanelSectionHeader {
                text: "EASY EFFECTS"
                foreground: root.bar.foreground
                fontFamily: root.bar.fontFamily
              }
              Text {
                visible: root.ready && !root.hasEffects
                width: parent.width
                wrapMode: Text.WordWrap
                textFormat: Text.PlainText
                text: "Easy Effects isn't installed. The presets still arrange the speakers; install it for the "
                  + "EQ presets and song leveling."
                color: Qt.darker(root.bar.foreground, 1.35)
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.bodySmall
              }
              Grid {
                id: eeGrid
                visible: root.hasEffects
                width: parent.width
                columns: 3
                spacing: Style.space(6)
                readonly property real cellWidth: (width - spacing * 2) / 3
                Repeater {
                  model: root.eeNames
                  Button {
                    required property var modelData
                    width: eeGrid.cellWidth
                    text: modelData.replace(/^Sounar /, "")
                    fontSize: Style.font.caption
                    foreground: root.bar.foreground
                    fontFamily: root.bar.fontFamily
                    bordered: true
                    selected: root.ready && root.st.effects.preset === modelData
                    hasCursor: root.isCursor("ee", modelData)
                    enabled: !root.busyMeasuring
                    tooltipText: root.ready && root.st.effects.preset_default === modelData
                      ? modelData + ": this preset's own Easy Effects preset" : "Use " + modelData + " with this preset"
                    onClicked: root.setEe(modelData)
                    onHovered: function(h) { if (h) root.cursorTo("ee", modelData) }
                  }
                }
              }
              Text {
                visible: root.hasEffects && root.st.effects.preset !== root.st.effects.preset_default
                width: parent.width
                wrapMode: Text.WordWrap
                textFormat: Text.PlainText
                text: root.ready ? "Changed from " + root.st.effects.preset_default + " for this preset. Reset puts it back." : ""
                color: Color.accent
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.caption
              }

              OptionRow {
                visible: root.hasEffects
                width: parent.width
                key: "leveling"
                label: "Song leveling"
                hint: root.levelingOn
                  ? "Quiet and loud songs at a similar loudness" + (root.ready && root.st.effects.target !== null ? " · " + root.minus(root.st.effects.target) + " LUFS for this preset" : "")
                  : "Off: every song plays at its own loudness"
                checked: root.levelingOn
                onToggled: root.toggleLeveling()
              }
              OptionRow {
                visible: root.hasEffects
                width: parent.width
                key: "effects"
                label: "Effects"
                hint: root.effectsOn ? "Easy Effects on" : "Off: only the speaker correction plays"
                checked: root.effectsOn
                onToggled: root.toggleEffects()
              }
            }

            // ---------- Speakers: balance, sync, measuring ----------
            Column {
              visible: root.setupDone && root.tab === "speakers"
              width: parent.width
              spacing: Style.space(10)

              Repeater {
                model: root.outputs
                OutputRow {
                  required property var modelData
                  width: parent.width
                  out: modelData
                }
              }

              PanelSeparator { visible: root.total > 1; foreground: root.bar.foreground }

              // Two outputs: one row each for balance and sync.
              AdjustRow {
                visible: root.pair
                width: parent.width
                type: "balance"
                title: "Balance"
                value: root.pairBalanceText
                hint: "0.5 dB a click. Calibrate sets it for your seat; the room presets keep in step."
                leftKey: root.pair ? "+:" + root.outputs[0].id : ""
                leftLabel: root.pair ? root.shortLabel(root.outputs[0]) + " louder" : ""
                leftIcon: root.iconMinus
                rightKey: root.pair ? "+:" + root.outputs[1].id : ""
                rightLabel: root.pair ? root.shortLabel(root.outputs[1]) + " louder" : ""
                rightIcon: root.iconPlus
                rowEnabled: !root.busyMeasuring && root.allConnected
                onLeftClicked: root.balance(root.outputs[0].id, 0.5)
                onRightClicked: root.balance(root.outputs[1].id, 0.5)
              }
              AdjustRow {
                visible: root.pair
                width: parent.width
                type: "nudge"
                title: "Sync by ear"
                value: root.syncText(root.outputs)
                hint: "1 ms a click, if voices sound doubled. Sync now measures it exactly."
                leftKey: root.pair ? "-:" + root.outputs[0].id : ""
                leftLabel: root.pair ? root.shortLabel(root.outputs[0]) + " sooner" : ""
                leftIcon: root.iconMinus
                rightKey: root.pair ? "+:" + root.outputs[0].id : ""
                rightLabel: root.pair ? root.shortLabel(root.outputs[0]) + " later" : ""
                rightIcon: root.iconPlus
                rowEnabled: !root.busyMeasuring
                onLeftClicked: root.nudge(root.outputs[0].id, -1)
                onRightClicked: root.nudge(root.outputs[0].id, 1)
              }

              // More outputs: a balance row for each, a sync row for each but the first.
              Repeater {
                model: root.total > 2 ? root.outputs : []
                AdjustRow {
                  required property var modelData
                  width: parent.width
                  type: "balance"
                  compact: true
                  title: root.shortLabel(modelData)
                  value: root.dbText(root.balanceOf(modelData))
                  hint: ""
                  leftKey: "-:" + modelData.id; leftLabel: "Quieter"; leftIcon: root.iconMinus
                  rightKey: "+:" + modelData.id; rightLabel: "Louder"; rightIcon: root.iconPlus
                  rowEnabled: !root.busyMeasuring && root.allConnected
                  onLeftClicked: root.balance(modelData.id, -0.5)
                  onRightClicked: root.balance(modelData.id, 0.5)
                }
              }
              Repeater {
                model: root.total > 2 ? root.outputs.slice(1) : []
                AdjustRow {
                  required property var modelData
                  width: parent.width
                  type: "nudge"
                  compact: true
                  title: "Sync · " + root.shortLabel(modelData)
                  value: "waits " + root.syncOf(modelData).toFixed(1) + " ms"
                  hint: ""
                  leftKey: "-:" + modelData.id; leftLabel: "Sooner"; leftIcon: root.iconMinus
                  rightKey: "+:" + modelData.id; rightLabel: "Later"; rightIcon: root.iconPlus
                  rowEnabled: !root.busyMeasuring
                  onLeftClicked: root.nudge(modelData.id, -1)
                  onRightClicked: root.nudge(modelData.id, 1)
                }
              }

              PanelSeparator { foreground: root.bar.foreground }

              Row {
                id: measureRow
                width: parent.width
                spacing: Style.space(6)
                readonly property real cellWidth: (width - spacing) / 2
                Button {
                  width: measureRow.cellWidth
                  iconText: root.iconSync
                  iconSpinning: root.measuringNow === "sync"
                  text: root.measuringNow === "sync" ? "Syncing…" : "Sync now"
                  fontSize: Style.font.bodySmall
                  foreground: root.bar.foreground
                  fontFamily: root.bar.fontFamily
                  bordered: true
                  enabled: !root.busyMeasuring && root.allConnected && root.total > 1
                  opacity: enabled || root.measuringNow === "sync" ? 1 : 0.45
                  hasCursor: root.isCursor("measure", "sync")
                  tooltipText: "After a Bluetooth reconnect: about 15 s of chirps, music paused"
                  onClicked: root.measure("sync")
                  onHovered: function(h) { if (h) root.cursorTo("measure", "sync") }
                }
                Button {
                  width: measureRow.cellWidth
                  iconText: root.iconTarget
                  iconSpinning: root.measuringNow === "calibrate"
                  text: root.measuringNow === "calibrate" ? "Calibrating…" : "Calibrate"
                  fontSize: Style.font.bodySmall
                  foreground: root.bar.foreground
                  fontFamily: root.bar.fontFamily
                  bordered: true
                  enabled: !root.busyMeasuring && root.allConnected
                  opacity: enabled || root.measuringNow === "calibrate" ? 1 : 0.45
                  hasCursor: root.isCursor("measure", "calibrate")
                  tooltipText: "Once, or after moving a speaker: about a minute of test sounds, music paused. Sit where you listen"
                  onClicked: root.measure("calibrate")
                  onHovered: function(h) { if (h) root.cursorTo("measure", "calibrate") }
                }
              }
              Row {
                id: backRow
                visible: root.total > 1
                width: parent.width
                spacing: Style.space(6)
                readonly property real cellWidth: (width - spacing) / 2
                Button {
                  width: backRow.cellWidth
                  iconText: root.iconPin
                  text: !root.syncInfo.stable ? "No stable sync yet" : (root.syncInfo.stable.active ? "On the stable sync" : "Stable sync")
                  fontSize: Style.font.caption
                  foreground: root.bar.foreground
                  fontFamily: root.bar.fontFamily
                  bordered: true
                  enabled: !!root.syncInfo.stable && !root.syncInfo.stable.active && !root.busyMeasuring
                  opacity: enabled || (root.syncInfo.stable && root.syncInfo.stable.active) ? 1 : 0.45
                  tooltipText: root.syncInfo.stable
                    ? "Back to " + root.syncInfo.stable.text + ", from the " + root.syncInfo.stable.what + " at "
                      + root.syncInfo.stable.ts.substring(11, 16) + ". Plays nothing; nothing ever syncs on its own"
                    : "A calibration or a Sync now that ends in sync sets it"
                  onClicked: root.run(["sync", "stable"])
                }
                Button {
                  width: backRow.cellWidth
                  iconText: root.iconUndo
                  text: root.syncInfo.undo ? "Back · " + root.syncInfo.undo.ts.substring(11, 16) : "Nothing to undo"
                  fontSize: Style.font.caption
                  foreground: root.bar.foreground
                  fontFamily: root.bar.fontFamily
                  bordered: true
                  enabled: !!root.syncInfo.undo && !root.busyMeasuring
                  opacity: enabled ? 1 : 0.45
                  tooltipText: root.syncInfo.undo
                    ? "Back to " + root.syncInfo.undo.text + ", from before the " + root.syncInfo.undo.what + ". Plays nothing"
                    : "Each sync, calibration or run of by-ear clicks can be undone here"
                  onClicked: root.run(["sync", "undo"])
                }
              }
              Text {
                width: parent.width
                wrapMode: Text.WordWrap
                textFormat: Text.PlainText
                text: root.calibrationText
                color: Qt.darker(root.bar.foreground, 1.45)
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.caption
              }

              PanelSeparator { foreground: root.bar.foreground }

              Row {
                id: fileRow
                width: parent.width
                spacing: Style.space(6)
                readonly property real cellWidth: (width - spacing * 2) / 3
                Button {
                  width: fileRow.cellWidth
                  iconText: root.iconEdit
                  text: "Edit presets"
                  fontSize: Style.font.caption
                  foreground: Qt.darker(root.bar.foreground, 1.2)
                  fontFamily: root.bar.fontFamily
                  hasCursor: root.isCursor("file", "presets")
                  tooltipText: "Your presets file: add presets or change the built-in ones. The menu follows by itself"
                  onClicked: root.openFile("presets")
                  onHovered: function(h) { if (h) root.cursorTo("file", "presets") }
                }
                Button {
                  width: fileRow.cellWidth
                  iconText: root.iconSpeakers
                  text: "Speaker map"
                  fontSize: Style.font.caption
                  foreground: Qt.darker(root.bar.foreground, 1.2)
                  fontFamily: root.bar.fontFamily
                  hasCursor: root.isCursor("file", "setup")
                  tooltipText: "Where each speaker sits (setup.toml). After moving one: Calibrate"
                  onClicked: root.openFile("setup")
                  onHovered: function(h) { if (h) root.cursorTo("file", "setup") }
                }
                Button {
                  width: fileRow.cellWidth
                  iconText: root.iconDoc
                  text: "How it works"
                  fontSize: Style.font.caption
                  foreground: Qt.darker(root.bar.foreground, 1.2)
                  fontFamily: root.bar.fontFamily
                  hasCursor: root.isCursor("file", "docs")
                  tooltipText: "Sounar's README"
                  onClicked: root.openFile("docs")
                  onHovered: function(h) { if (h) root.cursorTo("file", "docs") }
                }
              }
            }

            // ---------- Room: the map (speakers, walls) and the room measurement ----------
            Column {
              visible: root.setupDone && root.tab === "room"
              width: parent.width
              spacing: Style.space(8)

              PanelSectionHeader {
                text: "YOUR ROOM · FROM ABOVE"
                foreground: root.bar.foreground
                fontFamily: root.bar.fontFamily
              }
              RoomMap {
                width: parent.width
                height: Math.round(width * 0.8)
              }
              Text {
                width: parent.width
                wrapMode: Text.WordWrap
                textFormat: Text.PlainText
                text: root.roomCaption
                color: root.dragWall !== "" || root.dragSpeaker !== "" ? Color.accent : Qt.darker(root.bar.foreground, 1.3)
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.caption
              }
              Text {
                width: parent.width
                wrapMode: Text.WordWrap
                textFormat: Text.PlainText
                text: root.roomSummary()
                color: Qt.darker(root.bar.foreground, 1.3)
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.caption
              }
              Row {
                id: roomRow
                width: parent.width
                spacing: Style.space(6)
                readonly property real cellWidth: (width - spacing) / 2
                Button {
                  width: roomRow.cellWidth
                  iconText: root.iconRoom
                  iconSpinning: root.measuringNow === "room"
                  text: root.measuringNow === "room" ? "Measuring…" : (root.roomInfo ? "Measure again" : "Measure the room")
                  fontSize: Style.font.bodySmall
                  foreground: root.bar.foreground
                  fontFamily: root.bar.fontFamily
                  bordered: true
                  enabled: !root.busyMeasuring && root.allConnected
                  opacity: enabled || root.measuringNow === "room" ? 1 : 0.45
                  tooltipText: "About 30 s of sweeps from the computer's speaker and each speaker, music paused. Sit where you listen"
                  onClicked: root.measureRoom()
                }
                Button {
                  width: roomRow.cellWidth
                  iconText: root.iconTune
                  text: !root.roomInfo ? "Room correction" : (root.roomInfo.correction ? "Correction on" : "Correction off")
                  fontSize: Style.font.bodySmall
                  foreground: root.bar.foreground
                  fontFamily: root.bar.fontFamily
                  bordered: true
                  selected: !!root.roomInfo && root.roomInfo.correction
                  enabled: !!root.roomInfo && !root.busyMeasuring
                  opacity: enabled ? 1 : 0.45
                  tooltipText: "Gentle cuts for the room's bass booms at your seat (the same on every speaker, never a boost)"
                  onClicked: root.run(["room", "correction", root.roomInfo.correction ? "off" : "on"])
                }
              }
            }
          }
        }
      }
    }
  }

  // The room from above: you at the centre (the computer), the speakers by direction (and how far
  // the room measurement heard them), the walls with grips to drag.
  component RoomMap: Item {
    id: map
    readonly property real fitM: {
      var m = 2
      for (var i = 0; i < root.mapSpeakers.length; i++) m = Math.max(m, root.mapSpeakers[i].dist)
      var w = root.walls()
      if (w) m = Math.max(m, Math.min(4.5, Math.max(w.left, w.right, w.ahead, w.behind)))
      return Math.min(5, Math.ceil(m + 0.5))
    }
    property real maxM: 3
    Binding on maxM { when: root.dragWall === "" && root.dragSpeaker === ""; value: map.fitM; restoreMode: Binding.RestoreNone }
    Behavior on maxM { NumberAnimation { duration: 320; easing.type: Easing.OutCubic } }
    readonly property real cx: width / 2
    readonly property real cy: height / 2
    readonly property real radius: Math.max(10, Math.min(width, height) / 2 - Style.space(14))
    readonly property real pxPerM: radius / maxM
    function px(a, d) {
      var r = a * Math.PI / 180
      return Qt.point(cx + Math.sin(r) * d * pxPerM, cy - Math.cos(r) * d * pxPerM)
    }
    readonly property string paintKey: JSON.stringify([root.mapSpeakers, root.walls(), width, height, maxM,
      String(Color.accent), String(root.bar.foreground), root.dragWall, root.dragSpeaker])
    onPaintKeyChanged: floor.requestPaint()

    Canvas {
      id: floor
      anchors.fill: parent
      antialiasing: true
      onPaint: {
        var ctx = getContext("2d")
        ctx.reset()
        var fg = root.bar.foreground, ac = Color.accent
        function css(c, a) {
          return "rgba(" + Math.round(c.r * 255) + "," + Math.round(c.g * 255) + "," + Math.round(c.b * 255) + "," + a + ")"
        }
        var cx = map.cx, cy = map.cy, R = map.radius, k = map.pxPerM
        var glow = ctx.createRadialGradient(cx, cy, 0, cx, cy, R)
        glow.addColorStop(0, css(ac, 0.12))
        glow.addColorStop(1, css(ac, 0.0))
        ctx.fillStyle = glow
        ctx.beginPath(); ctx.arc(cx, cy, R, 0, 2 * Math.PI); ctx.fill()
        var wl = root.walls()
        if (wl) {
          var rx = cx - wl.left * k, ry = cy - wl.ahead * k
          var rw = (wl.left + wl.right) * k, rh = (wl.ahead + wl.behind) * k
          ctx.fillStyle = css(fg, 0.035)
          ctx.fillRect(rx, ry, rw, rh)
          ctx.strokeStyle = css(fg, 0.32)
          ctx.lineWidth = 1.5
          ctx.strokeRect(rx, ry, rw, rh)
          if (root.dragWall !== "") {
            ctx.strokeStyle = css(ac, 0.95)
            ctx.lineWidth = 2.5
            ctx.beginPath()
            if (root.dragWall === "left") { ctx.moveTo(rx, ry); ctx.lineTo(rx, ry + rh) }
            else if (root.dragWall === "right") { ctx.moveTo(rx + rw, ry); ctx.lineTo(rx + rw, ry + rh) }
            else if (root.dragWall === "ahead") { ctx.moveTo(rx, ry); ctx.lineTo(rx + rw, ry) }
            else { ctx.moveTo(rx, ry + rh); ctx.lineTo(rx + rw, ry + rh) }
            ctx.stroke()
          }
        }
        ctx.lineWidth = 1
        for (var m = 1; m < map.maxM - 0.05; m++) {
          ctx.strokeStyle = css(fg, 0.09)
          ctx.beginPath(); ctx.arc(cx, cy, m * k, 0, 2 * Math.PI); ctx.stroke()
        }
        ctx.fillStyle = css(fg, 0.38)
        ctx.font = Math.round(Style.font.caption * 0.92) + "px \"" + root.bar.fontFamily + "\""
        ctx.textAlign = "left"; ctx.textBaseline = "top"
        for (m = 1; m < map.maxM - 0.05; m++) ctx.fillText(m + " m", cx + 4, cy - m * k + 2)
        ctx.textAlign = "center"
        ctx.fillText("ahead", cx, Math.max(2, cy - R - Style.space(12)))
        for (var i = 0; i < root.mapSpeakers.length; i++) {
          var sp = root.mapSpeakers[i], p = map.px(sp.angle, sp.dist)
          ctx.strokeStyle = css(ac, sp.id === root.dragSpeaker ? 0.5 : 0.22)
          ctx.lineWidth = sp.id === root.dragSpeaker ? 2 : 1.4
          ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(p.x, p.y); ctx.stroke()
        }
        ctx.fillStyle = css(fg, 0.92)
        ctx.beginPath(); ctx.arc(cx, cy, 7, 0, 2 * Math.PI); ctx.fill()
        ctx.fillStyle = css(ac, 1)
        ctx.beginPath(); ctx.moveTo(cx, cy - 16); ctx.lineTo(cx - 5, cy - 10); ctx.lineTo(cx + 5, cy - 10); ctx.closePath(); ctx.fill()
        ctx.fillStyle = css(fg, 0.5)
        ctx.textBaseline = "top"
        ctx.fillText("you", cx, cy + 10)
      }
    }
    Repeater {
      model: root.mapSpeakers
      SpeakerDot {
        required property var modelData
        sp: modelData
        mapItem: map
      }
    }
    Repeater {
      model: ["left", "right", "ahead", "behind"]
      WallGrip {
        required property var modelData
        side: modelData
        mapItem: map
      }
    }
  }

  // A speaker on the map: drag it around you to its direction (the drop saves it in setup.toml).
  component SpeakerDot: Item {
    id: dot
    property var sp: null
    property var mapItem: null
    readonly property var pos: mapItem && sp ? mapItem.px(sp.angle, sp.dist) : Qt.point(0, 0)
    readonly property bool hot: root.dragSpeaker === (sp ? sp.id : "") || area.containsMouse
    width: Style.space(22); height: width
    x: pos.x - width / 2
    y: pos.y - height / 2
    Rectangle {
      anchors.fill: parent
      radius: width / 2
      color: dot.hot ? Color.accent : Util.alpha(Color.accent, 0.75)
      border.width: dot.sp && dot.sp.measured ? 0 : 1.5
      border.color: root.bar.foreground
    }
    Text {
      anchors.top: parent.bottom
      anchors.horizontalCenter: parent.horizontalCenter
      textFormat: Text.PlainText
      text: dot.sp ? dot.sp.label : ""
      color: root.bar.foreground
      font.family: root.bar.fontFamily
      font.pixelSize: Style.font.caption
    }
    MouseArea {
      id: area
      anchors.fill: parent
      anchors.margins: -Style.space(6)
      hoverEnabled: true
      preventStealing: true
      enabled: !root.busyMeasuring
      cursorShape: root.dragSpeaker === (dot.sp ? dot.sp.id : "") ? Qt.ClosedHandCursor : Qt.OpenHandCursor
      onPressed: root.dragSpeaker = dot.sp.id
      onPositionChanged: function(mouse) {
        if (root.dragSpeaker !== dot.sp.id || !dot.mapItem) return
        var pt = mapToItem(dot.mapItem, mouse.x, mouse.y)
        root.moveSpeaker(dot.sp.id, Math.atan2(pt.x - dot.mapItem.cx, dot.mapItem.cy - pt.y) * 180 / Math.PI)
      }
      onReleased: { if (root.dragSpeaker !== dot.sp.id) return; root.dragSpeaker = ""; root.dropSpeaker(dot.sp.id) }
      onCanceled: { if (root.dragSpeaker !== dot.sp.id) return; root.dragSpeaker = ""; root.dropSpeaker(dot.sp.id) }
    }
  }

  // A wall of the room on the map: drag its grip towards you or away (the drop saves it).
  component WallGrip: Item {
    id: grip
    property string side: ""
    property var mapItem: null
    readonly property var w: root.walls()
    readonly property bool vertical: side === "left" || side === "right"
    readonly property real k: mapItem ? mapItem.pxPerM : 1
    readonly property real mcx: mapItem ? mapItem.cx : 0
    readonly property real mcy: mapItem ? mapItem.cy : 0
    readonly property real lineX: !w ? 0 : side === "left" ? mcx - w.left * k
      : side === "right" ? mcx + w.right * k : mcx + (w.right - w.left) * k / 2
    readonly property real lineY: !w ? 0 : side === "ahead" ? mcy - w.ahead * k
      : side === "behind" ? mcy + w.behind * k : mcy + (w.behind - w.ahead) * k / 2
    readonly property bool hot: root.dragWall === side || area.containsMouse
    visible: !!w
    width: vertical ? Style.space(12) : Style.space(38)
    height: vertical ? Style.space(38) : Style.space(12)
    x: Math.max(0, Math.min((mapItem ? mapItem.width : 0) - width, lineX - width / 2))
    y: Math.max(0, Math.min((mapItem ? mapItem.height : 0) - height, lineY - height / 2))
    Rectangle {
      anchors.centerIn: parent
      width: grip.vertical ? Style.space(5) : Style.space(28)
      height: grip.vertical ? Style.space(28) : Style.space(5)
      radius: Math.min(width, height) / 2
      color: grip.hot ? Color.accent : Util.alpha(root.bar.foreground, 0.5)
    }
    MouseArea {
      id: area
      anchors.fill: parent
      anchors.margins: -Style.space(5)
      hoverEnabled: true
      preventStealing: true
      enabled: !root.busyMeasuring
      cursorShape: grip.vertical ? Qt.SizeHorCursor : Qt.SizeVerCursor
      onPressed: root.dragWall = grip.side
      onPositionChanged: function(mouse) {
        if (root.dragWall !== grip.side || !grip.mapItem) return
        var pt = mapToItem(grip.mapItem, mouse.x, mouse.y)
        var d = grip.side === "left" ? (grip.mcx - pt.x) / grip.k : grip.side === "right" ? (pt.x - grip.mcx) / grip.k
          : grip.side === "ahead" ? (grip.mcy - pt.y) / grip.k : (pt.y - grip.mcy) / grip.k
        root.moveWall(grip.side, d)
      }
      onReleased: { if (root.dragWall !== grip.side) return; root.dragWall = ""; root.dropWall() }
      onCanceled: { if (root.dragWall !== grip.side) return; root.dragWall = ""; root.dropWall() }
    }
  }

  // One preset: its icon and name; the number is its key (1-9), the dot means fine-tuned.
  component PresetTile: CursorSurface {
    id: tile
    property string pid: ""
    readonly property var preset: root.presetById(pid)
    readonly property bool isActive: pid === root.activeId
    readonly property bool isPending: pid === root.pendingPreset

    hasCursor: root.isCursor("preset", pid)
    current: isActive
    foreground: root.bar.foreground
    fill: Style.hoverFillFor(root.bar.foreground, Color.accent)
    currentFill: Style.selectedFillFor(root.bar.foreground, Color.accent)
    implicitHeight: tileCol.implicitHeight + Style.space(18)
    opacity: root.busyMeasuring && !isActive ? 0.45 : 1

    MouseArea {
      anchors.fill: parent
      hoverEnabled: true
      cursorShape: root.busyMeasuring ? Qt.ArrowCursor : Qt.PointingHandCursor
      onContainsMouseChanged: {
        if (containsMouse) { root.cursorTo("preset", tile.pid); root.hoverPreset = tile.pid }
        else if (root.hoverPreset === tile.pid) root.hoverPreset = ""
      }
      onClicked: root.setPreset(tile.pid)
    }

    Text {
      anchors.left: parent.left
      anchors.top: parent.top
      anchors.leftMargin: Style.space(6)
      anchors.topMargin: Style.space(4)
      textFormat: Text.PlainText
      text: String(root.presetIndex(tile.pid) + 1)
      color: Qt.darker(root.bar.foreground, 1.8)
      font.family: root.bar.fontFamily
      font.pixelSize: Style.font.caption
    }
    Rectangle {
      visible: !!tile.preset && tile.preset.tweaked
      anchors.right: parent.right
      anchors.top: parent.top
      anchors.rightMargin: Style.space(7)
      anchors.topMargin: Style.space(7)
      width: Style.space(6)
      height: width
      radius: width / 2
      color: Color.accent
    }

    Column {
      id: tileCol
      anchors.centerIn: parent
      width: parent.width - Style.space(10)
      spacing: Style.space(3)

      Text {
        id: tileIcon
        anchors.horizontalCenter: parent.horizontalCenter
        textFormat: Text.PlainText
        text: tile.preset ? tile.preset.icon : ""
        color: tile.isActive ? Color.accent : root.bar.foreground
        font.family: root.bar.fontFamily
        font.pixelSize: Style.font.heading
        scale: tile.isActive ? 1.12 : 1.0
        Behavior on scale { NumberAnimation { duration: 220; easing.type: Easing.OutBack } }
        Behavior on color { ColorAnimation { duration: 180 } }

        SequentialAnimation on opacity {
          running: tile.isPending
          loops: Animation.Infinite
          alwaysRunToEnd: true
          NumberAnimation { from: 1; to: 0.35; duration: 420; easing.type: Easing.InOutSine }
          NumberAnimation { from: 0.35; to: 1; duration: 420; easing.type: Easing.InOutSine }
        }
      }
      Text {
        width: parent.width
        horizontalAlignment: Text.AlignHCenter
        textFormat: Text.PlainText
        text: tile.preset ? tile.preset.label : ""
        color: root.bar.foreground
        opacity: tile.isActive ? 1 : 0.8
        font.family: root.bar.fontFamily
        font.pixelSize: Style.font.caption
        font.bold: tile.isActive
        elide: Text.ElideRight
      }
    }
  }

  // One value of the active preset on one line: name, slider (notch = the preset's value), value.
  component ParamRow: CursorSurface {
    id: pr
    property string pkey: ""
    readonly property var param: root.paramByKey(pkey)
    readonly property real shown: param ? (slider.dragging ? root.snap(param, slider.liveValue) : root.paramValue(param)) : 0
    readonly property bool changed: !!param && Math.abs(root.paramValue(param) - param.preset) > 1e-6

    visible: !!param
    hasCursor: root.isCursor("param", pkey)
    foreground: root.bar.foreground
    fill: Style.hoverFillFor(root.bar.foreground, Color.accent)
    implicitHeight: Math.max(prLabel.implicitHeight, slider.implicitHeight) + Style.space(4)
    opacity: root.busyMeasuring ? 0.45 : 1

    HoverHandler {
      onHoveredChanged: if (hovered && pr.param) { root.cursorTo("param", pr.pkey); root.hoverHint = pr.param.label + ": " + pr.param.hint }
    }

    Text {
      id: prLabel
      anchors.left: parent.left
      anchors.leftMargin: Style.space(8)
      anchors.verticalCenter: parent.verticalCenter
      width: Math.round(parent.width * 0.34)
      textFormat: Text.PlainText
      text: pr.param ? pr.param.label : ""
      color: root.bar.foreground
      font.family: root.bar.fontFamily
      font.pixelSize: Style.font.bodySmall
      elide: Text.ElideRight
    }
    Text {
      id: prValue
      anchors.right: parent.right
      anchors.rightMargin: Style.space(8)
      anchors.verticalCenter: parent.verticalCenter
      width: Style.space(66)
      horizontalAlignment: Text.AlignRight
      textFormat: Text.PlainText
      text: root.fmt(pr.param, pr.shown)
      color: pr.changed || slider.dragging ? Color.accent : Qt.darker(root.bar.foreground, 1.2)
      font.family: root.bar.fontFamily
      font.pixelSize: Style.font.bodySmall
      font.bold: pr.changed
    }

    Item {
      anchors.left: prLabel.right
      anchors.leftMargin: Style.space(6)
      anchors.right: prValue.left
      anchors.rightMargin: Style.space(8)
      anchors.verticalCenter: parent.verticalCenter
      height: slider.implicitHeight

      // The preset's own value.
      Rectangle {
        visible: !!pr.param
        width: Math.max(2, Style.space(2))
        height: slider.trackHeight + Style.space(10)
        radius: 1
        anchors.verticalCenter: parent.verticalCenter
        x: pr.param ? Math.max(0, Math.min(parent.width - width,
             parent.width * (pr.param.preset - pr.param.min) / (pr.param.max - pr.param.min) - width / 2)) : 0
        color: Util.alpha(root.bar.foreground, 0.45)
      }

      PanelSlider {
        id: slider
        anchors.fill: parent
        bar: root.bar
        minimum: pr.param ? pr.param.min : 0
        maximum: pr.param ? pr.param.max : 1
        step: pr.param ? pr.param.step : 0.1
        value: pr.param ? root.paramValue(pr.param) : 0
        fillColor: pr.changed ? Color.accent : root.bar.foreground
        enabled: !root.busyMeasuring
        onReleased: function(v) { root.setParam(pr.param, v) }
        onRightClicked: if (pr.param) root.setParam(pr.param, pr.param.preset)
      }
    }
  }

  // A switch with a label and a one-line hint; the whole row is clickable.
  component OptionRow: CursorSurface {
    id: opt
    property string key: ""
    property string label: ""
    property string hint: ""
    property bool checked: false
    signal toggled()

    hasCursor: root.isCursor("toggle", key)
    foreground: root.bar.foreground
    fill: Style.hoverFillFor(root.bar.foreground, Color.accent)
    implicitHeight: Math.max(optInfo.implicitHeight, optSwitch.implicitHeight) + Style.space(10)

    MouseArea {
      anchors.fill: parent
      hoverEnabled: true
      cursorShape: Qt.PointingHandCursor
      onContainsMouseChanged: if (containsMouse) root.cursorTo("toggle", opt.key)
      onClicked: opt.toggled()
    }
    Column {
      id: optInfo
      anchors.left: parent.left
      anchors.leftMargin: Style.space(8)
      anchors.right: optSwitch.left
      anchors.rightMargin: Style.space(10)
      anchors.verticalCenter: parent.verticalCenter
      spacing: Style.space(1)
      Text {
        textFormat: Text.PlainText
        text: opt.label
        color: root.bar.foreground
        font.family: root.bar.fontFamily
        font.pixelSize: Style.font.bodySmall
        width: parent.width
        elide: Text.ElideRight
      }
      Text {
        textFormat: Text.PlainText
        text: opt.hint
        color: Qt.darker(root.bar.foreground, 1.5)
        font.family: root.bar.fontFamily
        font.pixelSize: Style.font.caption
        width: parent.width
        wrapMode: Text.WordWrap
      }
    }
    ToggleSwitch {
      id: optSwitch
      anchors.right: parent.right
      anchors.rightMargin: Style.space(8)
      anchors.verticalCenter: parent.verticalCenter
      checked: opt.checked
      interactive: false
      foreground: root.bar.foreground
    }
  }

  // An output: connected or not, codec and volume; each of its speakers with where it sits and
  // how long this layout makes it wait.
  component OutputRow: Item {
    id: orow
    property var out: ({ speakers: [] })
    implicitHeight: Math.max(orIcon.implicitHeight, orInfo.implicitHeight)

    Text {
      id: orIcon
      anchors.left: parent.left
      anchors.top: parent.top
      width: Style.space(28)
      horizontalAlignment: Text.AlignHCenter
      textFormat: Text.PlainText
      text: orow.out.connected ? root.iconSpeaker : root.iconSpeakerOff
      color: orow.out.connected ? Color.accent : Color.urgent
      font.family: root.bar.fontFamily
      font.pixelSize: Style.font.heading
    }
    Column {
      id: orInfo
      anchors.left: orIcon.right
      anchors.leftMargin: Style.space(8)
      anchors.right: parent.right
      spacing: Style.space(1)
      Repeater {
        model: orow.out.speakers
        Item {
          required property var modelData
          width: orInfo.width
          implicitHeight: spkName.implicitHeight
          Text {
            id: spkName
            anchors.left: parent.left
            anchors.right: spkDelay.left
            anchors.rightMargin: Style.space(8)
            textFormat: Text.PlainText
            text: (modelData.nickname || modelData.name) + " · " + root.hourText(modelData)
            color: root.bar.foreground
            font.family: root.bar.fontFamily
            font.pixelSize: Style.font.body
            font.bold: true
            elide: Text.ElideRight
          }
          Text {
            id: spkDelay
            anchors.right: parent.right
            anchors.verticalCenter: spkName.verticalCenter
            textFormat: Text.PlainText
            text: orow.out.connected && modelData.delay_ms !== null ? "waits " + modelData.delay_ms.toFixed(1) + " ms" : ""
            color: Qt.darker(root.bar.foreground, 1.4)
            font.family: root.bar.fontFamily
            font.pixelSize: Style.font.caption
          }
        }
      }
      Text {
        textFormat: Text.PlainText
        text: orow.out.connected
          ? [orow.out.codec || orow.out.bus, orow.out.volume !== null ? orow.out.volume + "% volume" : ""]
              .filter(function(s) { return s !== "" }).join(" · ")
          : "Not connected · the others play plain stereo"
        color: orow.out.connected ? Qt.darker(root.bar.foreground, 1.5) : Color.urgent
        font.family: root.bar.fontFamily
        font.pixelSize: Style.font.caption
        width: parent.width
        elide: Text.ElideRight
      }
    }
  }

  // A value nudged with two buttons (balance, sync).
  component AdjustRow: Item {
    id: ar
    property string type: ""
    property string title: ""
    property string value: ""
    property string hint: ""
    property string leftKey: ""
    property string leftLabel: ""
    property string leftIcon: ""
    property string rightKey: ""
    property string rightLabel: ""
    property string rightIcon: ""
    property bool rowEnabled: true
    property bool compact: false          // one line: title, value, two small buttons
    signal leftClicked()
    signal rightClicked()

    implicitHeight: compact ? arLine.implicitHeight : arCol.implicitHeight

    Item {
      id: arLine
      visible: ar.compact
      width: parent.width
      implicitHeight: Math.max(arLineTitle.implicitHeight, arPlus.implicitHeight)
      Text {
        id: arLineTitle
        anchors.left: parent.left
        anchors.right: arLineValue.left
        anchors.rightMargin: Style.space(8)
        anchors.verticalCenter: parent.verticalCenter
        textFormat: Text.PlainText
        text: ar.title
        color: root.bar.foreground
        font.family: root.bar.fontFamily
        font.pixelSize: Style.font.bodySmall
        elide: Text.ElideRight
      }
      Text {
        id: arLineValue
        anchors.right: arMinus.left
        anchors.rightMargin: Style.space(10)
        anchors.verticalCenter: parent.verticalCenter
        textFormat: Text.PlainText
        text: ar.value
        color: Color.accent
        font.family: root.bar.fontFamily
        font.pixelSize: Style.font.bodySmall
        font.bold: true
      }
      Button {
        id: arMinus
        anchors.right: arPlus.left
        anchors.rightMargin: Style.space(6)
        anchors.verticalCenter: parent.verticalCenter
        iconText: ar.leftIcon
        tooltipText: ar.leftLabel
        foreground: root.bar.foreground
        fontFamily: root.bar.fontFamily
        bordered: true
        enabled: ar.rowEnabled
        opacity: enabled ? 1 : 0.45
        hasCursor: root.isCursor(ar.type, ar.leftKey)
        onClicked: ar.leftClicked()
        onHovered: function(h) { if (h) root.cursorTo(ar.type, ar.leftKey) }
      }
      Button {
        id: arPlus
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        iconText: ar.rightIcon
        tooltipText: ar.rightLabel
        foreground: root.bar.foreground
        fontFamily: root.bar.fontFamily
        bordered: true
        enabled: ar.rowEnabled
        opacity: enabled ? 1 : 0.45
        hasCursor: root.isCursor(ar.type, ar.rightKey)
        onClicked: ar.rightClicked()
        onHovered: function(h) { if (h) root.cursorTo(ar.type, ar.rightKey) }
      }
    }

    Column {
      id: arCol
      visible: !ar.compact
      width: parent.width
      spacing: Style.space(6)

      Item {
        width: parent.width
        implicitHeight: Math.max(arTitle.implicitHeight, arValue.implicitHeight)
        Text {
          id: arTitle
          anchors.left: parent.left
          anchors.verticalCenter: parent.verticalCenter
          textFormat: Text.PlainText
          text: ar.title
          color: root.bar.foreground
          font.family: root.bar.fontFamily
          font.pixelSize: Style.font.bodySmall
        }
        Text {
          id: arValue
          anchors.right: parent.right
          anchors.verticalCenter: parent.verticalCenter
          textFormat: Text.PlainText
          text: ar.value
          color: Color.accent
          font.family: root.bar.fontFamily
          font.pixelSize: Style.font.bodySmall
          font.bold: true
        }
      }
      Row {
        id: arButtons
        width: parent.width
        spacing: Style.space(6)
        readonly property real cellWidth: (width - spacing) / 2
        Button {
          width: arButtons.cellWidth
          iconText: ar.leftIcon
          text: ar.leftLabel
          fontSize: Style.font.caption
          foreground: root.bar.foreground
          fontFamily: root.bar.fontFamily
          bordered: true
          enabled: ar.rowEnabled
          opacity: enabled ? 1 : 0.45
          hasCursor: root.isCursor(ar.type, ar.leftKey)
          onClicked: ar.leftClicked()
          onHovered: function(h) { if (h) root.cursorTo(ar.type, ar.leftKey) }
        }
        Button {
          width: arButtons.cellWidth
          iconText: ar.rightIcon
          text: ar.rightLabel
          fontSize: Style.font.caption
          foreground: root.bar.foreground
          fontFamily: root.bar.fontFamily
          bordered: true
          enabled: ar.rowEnabled
          opacity: enabled ? 1 : 0.45
          hasCursor: root.isCursor(ar.type, ar.rightKey)
          onClicked: ar.rightClicked()
          onHovered: function(h) { if (h) root.cursorTo(ar.type, ar.rightKey) }
        }
      }
      Text {
        visible: ar.hint !== ""
        width: parent.width
        wrapMode: Text.WordWrap
        textFormat: Text.PlainText
        text: ar.hint
        color: Qt.darker(root.bar.foreground, 1.5)
        font.family: root.bar.fontFamily
        font.pixelSize: Style.font.caption
      }
    }
  }
}
