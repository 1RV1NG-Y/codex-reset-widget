// Top-bar panel for the Codex Widget daemon.
//
// The Python daemon owns every account read and timer; this extension only
// renders the JSON snapshot it publishes on D-Bus and sends small commands
// back. All D-Bus traffic is async so a busy daemon never stalls the shell.

import GObject from 'gi://GObject';
import GLib from 'gi://GLib';
import Gio from 'gi://Gio';
import St from 'gi://St';
import Clutter from 'gi://Clutter';
import Pango from 'gi://Pango';
import Cairo from 'cairo';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';

const BUS_NAME = 'io.github.codex_widget.CodexWidget';
const OBJECT_PATH = '/io/github/codex_widget/CodexWidget/Panel';
const IFACE = 'io.github.codex_widget.Panel1';
const SERVICE = 'codex-widget.service';
const CLAUDE_USAGE_PAGE = 'https://claude.ai/settings/usage';

const PROVIDERS = {
    codex: {label: 'Codex', rgb: [0.42, 0.66, 1.0]},
    claude: {label: 'Claude', rgb: [0.85, 0.47, 0.34]},
};
const DANGER_RGB = [0.95, 0.42, 0.38];
const TICK_SECONDS = 30;
const OPEN_REFRESH_SECONDS = 60;

function nowSeconds() {
    return Date.now() / 1000;
}

function formatSpan(seconds) {
    if (seconds === null || seconds === undefined)
        return '—';
    if (seconds <= 0)
        return 'now';
    const minutes = Math.floor(seconds / 60);
    if (minutes < 1)
        return '<1m';
    const hours = Math.floor(minutes / 60);
    if (hours < 1)
        return `${minutes}m`;
    const days = Math.floor(hours / 24);
    if (days < 1)
        return `${hours}h ${minutes % 60}m`;
    return `${days}d ${hours % 24}h`;
}

function until(epoch) {
    return epoch ? formatSpan(epoch - nowSeconds()) : null;
}

function ago(epoch) {
    if (!epoch)
        return null;
    const span = nowSeconds() - epoch;
    return span < 60 ? 'just now' : `${formatSpan(span)} ago`;
}

function clip(text, limit) {
    text = String(text ?? '').trim();
    return text.length > limit ? `${text.slice(0, limit).trimEnd()}…` : text;
}

function scaleFactor() {
    return St.ThemeContext.get_for_stage(global.stage).scale_factor;
}

function openUri(uri) {
    try {
        Gio.AppInfo.launch_default_for_uri(uri, global.create_app_launch_context(0, -1));
    } catch (e) {
        logError(e, 'codex-widget: could not open link');
    }
}

// A usage ring: thick arc for usage, a small dot on the track for how much
// of the window's time has elapsed. Arc ahead of the dot = ahead of pace.
const UsageRing = GObject.registerClass(
class UsageRing extends St.Widget {
    _init() {
        super._init({
            style_class: 'cw-ring',
            layout_manager: new Clutter.BinLayout(),
        });
        this._fraction = 0;
        this._pace = null;
        this._rgb = PROVIDERS.codex.rgb;

        this._area = new St.DrawingArea({x_expand: true, y_expand: true});
        this._area.connect('repaint', area => this._repaint(area));
        this.add_child(this._area);

        const center = new St.BoxLayout({
            orientation: Clutter.Orientation.VERTICAL,
            x_align: Clutter.ActorAlign.CENTER,
            y_align: Clutter.ActorAlign.CENTER,
        });
        this._value = new St.Label({style_class: 'cw-ring-value', x_align: Clutter.ActorAlign.CENTER});
        this._caption = new St.Label({style_class: 'cw-ring-caption', x_align: Clutter.ActorAlign.CENTER});
        this._caption.clutter_text.ellipsize = Pango.EllipsizeMode.NONE;
        center.add_child(this._value);
        center.add_child(this._caption);
        this.add_child(center);
    }

    update({percent, pace, rgb, loading}) {
        const known = percent !== null && percent !== undefined;
        this._fraction = known ? Math.max(0, Math.min(1, percent / 100)) : 0;
        this._pace = pace;
        this._rgb = known && percent >= 90 ? DANGER_RGB : rgb;
        this._value.text = known ? `${Math.round(percent)}%` : (loading ? '···' : '—');
        this._caption.text = known ? 'used' : (loading ? 'checking' : 'no data');
        this._area.queue_repaint();
    }

    _repaint(area) {
        const cr = area.get_context();
        const [width, height] = area.get_surface_size();
        const s = scaleFactor();
        const line = 9 * s;
        const radius = Math.min(width, height) / 2 - line / 2 - s;
        const cx = width / 2;
        const cy = height / 2;
        const start = -Math.PI / 2;
        const [r, g, b] = this._rgb;

        cr.setLineCap(Cairo.LineCap.ROUND);
        cr.setLineWidth(line);
        cr.setSourceRGBA(1, 1, 1, 0.08);
        cr.arc(cx, cy, radius, 0, 2 * Math.PI);
        cr.stroke();

        if (this._fraction > 0) {
            // Soft glow under the arc, then the arc itself.
            const end = start + 2 * Math.PI * Math.max(this._fraction, 0.004);
            cr.setLineWidth(line + 6 * s);
            cr.setSourceRGBA(r, g, b, 0.12);
            cr.arc(cx, cy, radius, start, end);
            cr.stroke();
            cr.setLineWidth(line);
            cr.setSourceRGBA(r, g, b, 1);
            cr.arc(cx, cy, radius, start, end);
            cr.stroke();
        }

        if (this._pace !== null) {
            const angle = start + 2 * Math.PI * this._pace;
            const x = cx + radius * Math.cos(angle);
            const y = cy + radius * Math.sin(angle);
            cr.setSourceRGBA(0.08, 0.08, 0.1, 0.9);
            cr.arc(x, y, 3.6 * s, 0, 2 * Math.PI);
            cr.fill();
            cr.setSourceRGBA(1, 1, 1, 0.95);
            cr.arc(x, y, 2.2 * s, 0, 2 * Math.PI);
            cr.fill();
        }
        cr.$dispose();
    }
});

const Toggle = GObject.registerClass(
class Toggle extends St.Button {
    _init() {
        super._init({
            style_class: 'cw-switch',
            toggle_mode: true,
            can_focus: true,
            y_align: Clutter.ActorAlign.CENTER,
        });
        this._knob = new St.Widget({style_class: 'cw-switch-knob', x_expand: true});
        this.set_child(this._knob);
        this.connect('notify::checked', () => this._sync());
        this._sync();
    }

    _sync() {
        this._knob.x_align = this.checked ? Clutter.ActorAlign.END : Clutter.ActorAlign.START;
    }
});

function detailRow(title) {
    const row = new St.BoxLayout({style_class: 'cw-row', x_expand: true});
    const label = new St.Label({
        style_class: 'cw-row-label', text: title,
        x_expand: true, y_align: Clutter.ActorAlign.CENTER,
    });
    const value = new St.Button({style_class: 'cw-row-value', reactive: false, can_focus: false});
    value.set_child(new St.Label({text: '—'}));
    row.add_child(label);
    row.add_child(value);
    return {row, label, value};
}

const UsageIndicator = GObject.registerClass(
class UsageIndicator extends PanelMenu.Button {
    _init(extension) {
        super._init(0.5, 'Codex & Claude Usage', false);
        this._extension = extension;
        this._state = null;
        this._online = false;
        this._tickSource = 0;
        this._ticks = 0;
        this._spinning = false;

        const box = new St.BoxLayout({style_class: 'panel-status-menu-box cw-panel-box'});
        this._panelIcon = new St.Icon({
            gicon: Gio.icon_new_for_string(`${extension.path}/icons/codex-widget-symbolic.svg`),
            style_class: 'system-status-icon',
        });
        this._panelLabel = new St.Label({
            style_class: 'cw-panel-label',
            y_align: Clutter.ActorAlign.CENTER,
            visible: false,
        });
        box.add_child(this._panelIcon);
        box.add_child(this._panelLabel);
        this.add_child(box);

        this._buildMenu();
        this.menu.connect('open-state-changed', (_menu, open) => this._onOpenChanged(open));

        this._signalId = Gio.DBus.session.signal_subscribe(
            BUS_NAME, IFACE, 'StateChanged', OBJECT_PATH, null,
            Gio.DBusSignalFlags.NONE,
            (_c, _s, _p, _i, _n, params) => this._setState(params.deepUnpack()[0]));
        this._watchId = Gio.bus_watch_name(
            Gio.BusType.SESSION, BUS_NAME, Gio.BusNameWatcherFlags.NONE,
            () => this._onDaemonAppeared(),
            () => this._onDaemonVanished());
    }

    // ---- D-Bus -----------------------------------------------------------

    _call(method, params = null) {
        return new Promise((resolve, reject) => {
            Gio.DBus.session.call(
                BUS_NAME, OBJECT_PATH, IFACE, method, params, null,
                Gio.DBusCallFlags.NONE, 8000, null,
                (connection, result) => {
                    try {
                        resolve(connection.call_finish(result));
                    } catch (e) {
                        reject(e);
                    }
                });
        });
    }

    _send(method, params = null) {
        this._call(method, params).catch(e => {
            if (this._online)
                console.warn(`codex-widget: ${method} failed: ${e.message}`);
        });
    }

    async _onDaemonAppeared() {
        this._online = true;
        this._clearRetry();
        try {
            await this._call('AttachPanel', new GLib.Variant('(b)', [true]));
            const reply = await this._call('GetState');
            this._setState(reply.deepUnpack()[0]);
        } catch (e) {
            // An older daemon without the Panel object, or one still starting.
            console.warn(`codex-widget: daemon not ready: ${e.message}`);
            if (this._online) {
                this._retrySource = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 3, () => {
                    this._retrySource = 0;
                    if (this._online)
                        this._onDaemonAppeared();
                    return GLib.SOURCE_REMOVE;
                });
            }
            return;
        }
        if (this.menu.isOpen)
            this._requestRefresh(false);
    }

    _clearRetry() {
        if (this._retrySource) {
            GLib.source_remove(this._retrySource);
            this._retrySource = 0;
        }
    }

    _onDaemonVanished() {
        this._clearRetry();
        this._online = false;
        this._state = null;
        this._render();
    }

    _setState(json) {
        try {
            this._state = JSON.parse(json);
        } catch (e) {
            logError(e, 'codex-widget: bad state payload');
            return;
        }
        this._render();
    }

    _requestRefresh(force) {
        if (!this._online || !this._state)
            return;
        this._send('Refresh', new GLib.Variant('(sb)', [this._state.selected, force]));
    }

    // ---- Menu construction ---------------------------------------------

    _buildMenu() {
        this.menu.box.add_style_class_name('cw-menu');
        this._content = new St.BoxLayout({
            style_class: 'cw-content',
            orientation: Clutter.Orientation.VERTICAL,
        });
        this.menu.box.add_child(this._content);

        // Header: provider switch + refresh.
        const header = new St.BoxLayout({style_class: 'cw-header', x_expand: true});
        this._segment = new St.BoxLayout({style_class: 'cw-segment'});
        this._segmentButtons = {};
        for (const [id, provider] of Object.entries(PROVIDERS)) {
            const button = new St.Button({
                style_class: 'cw-segment-button', toggle_mode: false, can_focus: true,
            });
            const inner = new St.BoxLayout({style_class: 'cw-segment-inner'});
            inner.add_child(new St.Widget({
                style_class: `cw-dot cw-dot-${id}`, y_align: Clutter.ActorAlign.CENTER,
            }));
            inner.add_child(new St.Label({text: provider.label, y_align: Clutter.ActorAlign.CENTER}));
            button.set_child(inner);
            button.connect('clicked', () => this._selectProvider(id));
            this._segment.add_child(button);
            this._segmentButtons[id] = button;
        }
        header.add_child(this._segment);
        header.add_child(new St.Widget({x_expand: true}));

        this._refreshIcon = new St.Icon({icon_name: 'view-refresh-symbolic', style_class: 'cw-icon'});
        this._refreshIcon.set_pivot_point(0.5, 0.5);
        this._refreshButton = new St.Button({
            style_class: 'cw-icon-button', can_focus: true, child: this._refreshIcon,
            y_align: Clutter.ActorAlign.CENTER,
        });
        this._refreshButton.connect('clicked', () => this._requestRefresh(true));
        header.add_child(this._refreshButton);
        this._content.add_child(header);

        // Error banner.
        this._banner = new St.Label({style_class: 'cw-banner', visible: false, x_expand: true});
        this._banner.clutter_text.line_wrap = true;
        this._content.add_child(this._banner);

        // Gauges.
        const gauges = new St.BoxLayout({style_class: 'cw-gauges', x_expand: true});
        this._gauges = {};
        for (const [id, title] of [['five', '5-hour'], ['weekly', 'Weekly']]) {
            const column = new St.BoxLayout({
                style_class: 'cw-gauge', orientation: Clutter.Orientation.VERTICAL, x_expand: true,
            });
            const ring = new UsageRing();
            ring.x_align = Clutter.ActorAlign.CENTER;
            const name = new St.Label({style_class: 'cw-gauge-title', text: title, x_align: Clutter.ActorAlign.CENTER});
            const sub = new St.Label({style_class: 'cw-gauge-sub', text: '', x_align: Clutter.ActorAlign.CENTER});
            column.add_child(ring);
            column.add_child(name);
            column.add_child(sub);
            gauges.add_child(column);
            this._gauges[id] = {ring, sub};
        }
        this._content.add_child(gauges);

        // Details card.
        const details = new St.BoxLayout({
            style_class: 'cw-card', orientation: Clutter.Orientation.VERTICAL, x_expand: true,
        });
        this._bankedRow = detailRow('Banked resets');
        this._resetRow = detailRow('Last Tibo reset');
        this._offersRow = detailRow('Limit reset offers');
        this._offersRow.value.reactive = true;
        this._offersRow.value.can_focus = true;
        this._offersRow.value.add_style_class_name('cw-link');
        this._offersRow.value.child.text = 'Open usage ↗';
        this._offersRow.value.connect('clicked', () => {
            this.menu.close();
            openUri(CLAUDE_USAGE_PAGE);
        });
        this._resetRow.value.connect('clicked', () => {
            const url = this._provider()?.last_reset?.url;
            if (url) {
                this.menu.close();
                openUri(url);
            }
        });
        for (const item of [this._bankedRow, this._resetRow, this._offersRow])
            details.add_child(item.row);
        this._content.add_child(details);

        // Auto-roll card.
        const roll = new St.BoxLayout({style_class: 'cw-card cw-roll', x_expand: true});
        const rollText = new St.BoxLayout({orientation: Clutter.Orientation.VERTICAL, x_expand: true});
        rollText.add_child(new St.Label({style_class: 'cw-roll-title', text: '5-hour auto-roll'}));
        this._rollMessage = new St.Label({style_class: 'cw-roll-message', text: ''});
        this._rollMessage.clutter_text.line_wrap = true;
        rollText.add_child(this._rollMessage);
        this._rollToggle = new Toggle();
        this._rollToggle.connect('clicked', () => this._toggleAutoRoll());
        roll.add_child(rollText);
        roll.add_child(this._rollToggle);
        this._content.add_child(roll);

        // Footer.
        const footer = new St.BoxLayout({style_class: 'cw-footer', x_expand: true});
        this._footnote = new St.Label({
            style_class: 'cw-footnote', x_expand: true, y_align: Clutter.ActorAlign.CENTER,
        });
        const openButton = new St.Button({style_class: 'cw-pill-button', can_focus: true});
        const openInner = new St.BoxLayout({style_class: 'cw-pill-inner'});
        openInner.add_child(new St.Icon({icon_name: 'window-new-symbolic', style_class: 'cw-pill-icon'}));
        openInner.add_child(new St.Label({text: 'Widget', y_align: Clutter.ActorAlign.CENTER}));
        openButton.set_child(openInner);
        openButton.connect('clicked', () => {
            this.menu.close();
            this._send('OpenWidget');
        });
        footer.add_child(this._footnote);
        footer.add_child(openButton);
        this._content.add_child(footer);
        this._onlineWidgets = [header, gauges, details, roll, footer];

        // Offline view.
        this._offline = new St.BoxLayout({
            style_class: 'cw-offline', orientation: Clutter.Orientation.VERTICAL, x_expand: true,
        });
        this._offline.add_child(new St.Icon({
            gicon: this._panelIcon.gicon, style_class: 'cw-offline-icon', x_align: Clutter.ActorAlign.CENTER,
        }));
        this._offline.add_child(new St.Label({
            style_class: 'cw-offline-title', text: 'Usage daemon isn’t running', x_align: Clutter.ActorAlign.CENTER,
        }));
        const offlineSub = new St.Label({
            style_class: 'cw-offline-sub', text: `Start ${SERVICE} to see Codex and Claude usage.`,
            x_align: Clutter.ActorAlign.CENTER,
        });
        offlineSub.clutter_text.line_wrap = true;
        offlineSub.clutter_text.set_line_alignment(Pango.Alignment.CENTER);
        this._offline.add_child(offlineSub);
        const start = new St.Button({
            style_class: 'cw-pill-button cw-pill-accent', label: 'Start service',
            can_focus: true, x_align: Clutter.ActorAlign.CENTER,
        });
        start.connect('clicked', () => {
            try {
                Gio.Subprocess.new(['systemctl', '--user', 'start', SERVICE], Gio.SubprocessFlags.NONE);
            } catch (e) {
                logError(e, 'codex-widget: could not start the service');
            }
        });
        this._offline.add_child(start);
        this._content.add_child(this._offline);

        this._render();
    }

    // ---- Actions -------------------------------------------------------

    _provider() {
        return this._state?.providers?.[this._state.selected] ?? null;
    }

    _selectProvider(id) {
        if (!this._state || !this._state.providers[id] || this._state.selected === id)
            return;
        this._state.selected = id;
        this._render();
        this._send('SetProvider', new GLib.Variant('(s)', [id]));
    }

    _toggleAutoRoll() {
        const provider = this._provider();
        if (!provider)
            return;
        const enabled = this._rollToggle.checked;
        provider.auto_roll.enabled = enabled;
        provider.auto_roll.message = enabled ? 'Checking the 5-hour window…' : 'Automatic 5-hour rolling is off';
        this._render();
        this._send('SetAutoRoll', new GLib.Variant('(sb)', [this._state.selected, enabled]));
    }

    _onOpenChanged(open) {
        if (this._tickSource) {
            GLib.source_remove(this._tickSource);
            this._tickSource = 0;
        }
        if (!open)
            return;
        this._render();
        this._requestRefresh(false);
        this._ticks = 0;
        this._tickSource = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, TICK_SECONDS, () => {
            this._ticks += 1;
            this._render();
            if ((this._ticks * TICK_SECONDS) % OPEN_REFRESH_SECONDS === 0)
                this._requestRefresh(false);
            return GLib.SOURCE_CONTINUE;
        });
    }

    _spin(active) {
        if (active === this._spinning)
            return;
        this._spinning = active;
        if (active)
            this._spinOnce();
    }

    _spinOnce() {
        this._refreshIcon.rotation_angle_z = 0;
        this._refreshIcon.ease({
            rotation_angle_z: 360,
            duration: 900,
            mode: Clutter.AnimationMode.EASE_IN_OUT_QUAD,
            onStopped: isFinished => {
                if (isFinished && this._spinning)
                    this._spinOnce();
                else
                    this._refreshIcon.rotation_angle_z = 0;
            },
        });
    }

    // ---- Rendering -----------------------------------------------------

    _render() {
        const state = this._state;
        const online = this._online && state !== null;
        for (const widget of this._onlineWidgets)
            widget.visible = online;
        this._offline.visible = !online;
        if (!online) {
            this._banner.visible = false;
            this._panelLabel.visible = false;
            this._spin(false);
            return;
        }

        const selected = state.selected;
        const provider = state.providers[selected];
        const usage = provider.usage;
        const rgb = PROVIDERS[selected].rgb;

        // Panel button: the binding 5-hour number at a glance.
        const fivePercent = usage?.five_hour_percent;
        this._panelLabel.visible = fivePercent !== null && fivePercent !== undefined;
        if (this._panelLabel.visible)
            this._panelLabel.text = `${Math.round(fivePercent)}%`;

        if (!this.menu.isOpen && this._content.mapped === false)
            return;

        for (const name of Object.keys(PROVIDERS))
            this._content.remove_style_class_name(`cw-${name}`);
        this._content.add_style_class_name(`cw-${selected}`);

        this._segment.visible = Boolean(state.claude_available);
        for (const [id, button] of Object.entries(this._segmentButtons)) {
            button.visible = Boolean(state.providers[id]);
            if (id === selected)
                button.add_style_pseudo_class('checked');
            else
                button.remove_style_pseudo_class('checked');
        }
        this._spin(Boolean(provider.loading));

        this._banner.visible = Boolean(provider.error);
        this._banner.text = clip(provider.error, 160);

        const now = nowSeconds();
        const pace = (resetAt, windowMinutes) => {
            if (!resetAt || !windowMinutes)
                return null;
            const total = windowMinutes * 60;
            const left = resetAt - now;
            return left > 0 && left <= total ? 1 - left / total : null;
        };
        this._gauges.five.ring.update({
            percent: usage?.five_hour_percent, rgb, loading: provider.loading,
            pace: pace(usage?.five_hour_reset_at, usage?.five_hour_window_minutes),
        });
        this._gauges.weekly.ring.update({
            percent: usage?.weekly_percent, rgb, loading: provider.loading,
            pace: pace(usage?.weekly_reset_at, usage?.weekly_window_minutes),
        });
        const resetText = epoch => {
            const left = until(epoch);
            return left ? (left === 'now' ? 'Resetting now' : `Resets in ${left}`) : ' ';
        };
        this._gauges.five.sub.text = resetText(usage?.five_hour_reset_at);
        this._gauges.weekly.sub.text = resetText(usage?.weekly_reset_at);

        const codex = selected === 'codex';
        this._bankedRow.row.visible = codex;
        const banked = usage?.banked_resets;
        this._bankedRow.value.child.text = banked === null || banked === undefined ? '—' : String(banked);
        this._offersRow.row.visible = !codex;

        this._resetRow.label.text = codex ? 'Last Tibo reset' : 'Last account reset';
        const lastReset = provider.last_reset;
        const hasLink = Boolean(codex && lastReset?.url);
        this._resetRow.value.child.text = (ago(lastReset?.at) ?? 'Unknown') + (hasLink ? ' ↗' : '');
        this._resetRow.value.reactive = hasLink;
        this._resetRow.value.can_focus = hasLink;
        if (hasLink)
            this._resetRow.value.add_style_class_name('cw-link');
        else
            this._resetRow.value.remove_style_class_name('cw-link');

        this._rollToggle.checked = Boolean(provider.auto_roll?.enabled);
        this._rollMessage.text = clip(provider.auto_roll?.message, 120);

        if (provider.loading)
            this._footnote.text = 'Updating…';
        else if (usage?.checked_at)
            this._footnote.text = `Updated ${ago(usage.checked_at)}`;
        else
            this._footnote.text = 'Not checked yet';
    }

    destroy() {
        this._clearRetry();
        if (this._tickSource) {
            GLib.source_remove(this._tickSource);
            this._tickSource = 0;
        }
        this._spinning = false;
        this._refreshIcon?.remove_all_transitions();
        if (this._signalId) {
            Gio.DBus.session.signal_unsubscribe(this._signalId);
            this._signalId = 0;
        }
        if (this._watchId) {
            Gio.bus_unwatch_name(this._watchId);
            this._watchId = 0;
        }
        if (this._online)
            this._send('AttachPanel', new GLib.Variant('(b)', [false]));
        this._online = false;
        super.destroy();
    }
});

export default class CodexUsageExtension extends Extension {
    enable() {
        this._indicator = new UsageIndicator(this);
        Main.panel.addToStatusArea(this.uuid, this._indicator, 1, 'right');
    }

    disable() {
        this._indicator?.destroy();
        this._indicator = null;
    }
}
