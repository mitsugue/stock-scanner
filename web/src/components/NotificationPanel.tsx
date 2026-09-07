import React from 'react';
import {
  compactNotificationFeed, dismissNotification, listNotifications, markAllSeen, SEV_JA, SEV_TONE,
  type CompactNotification,
} from '../lib/notifications';
import './NotificationPanel.css';

// Lean v13 — a first-class page projection of the device-local notification
// store. Detection/storage stay unchanged; this component performs no polling.

const jstFormatter = new Intl.DateTimeFormat('en-CA', {
  timeZone: 'Asia/Tokyo', year: 'numeric', month: '2-digit', day: '2-digit',
  hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
});

function jstStamp(value: string | number | Date) {
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return { day: '', time: '—' };
  const parts = Object.fromEntries(jstFormatter.formatToParts(date)
    .map((part) => [part.type, part.value]));
  return { day: `${parts.year}-${parts.month}-${parts.day}`,
    time: `${parts.hour}:${parts.minute}` };
}

export const NotificationPanel: React.FC = () => {
  const [, bump] = React.useReducer((x: number) => x + 1, 0);
  const items = compactNotificationFeed(listNotifications());
  React.useEffect(() => { markAllSeen(); }, []);
  const today = jstStamp(new Date()).day;
  const groups: [string, CompactNotification[]][] = [
    ['今日', items.filter((i) => jstStamp(i.createdAt).day === today)],
    ['それ以前', items.filter((i) => jstStamp(i.createdAt).day !== today)],
  ];
  return (
    <section id="asset-alerts" className="notification-center card" aria-label="通知">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        {/* v13.5.60 (owner): named by what it holds — the device-local record of
            銘柄 and judgment changes — not by where it is stored. */}
        <b style={{ fontSize: 13 }}>銘柄・判断の変化</b>
        <span className="notification-center__privacy">DEVICE LOCAL</span>
      </div>
      {items.length === 0 && (
        <p style={{ fontSize: 12, color: 'var(--text-faint)', margin: '8px 0' }}>
          新しい重要通知はありません。結論または重要条件が変わった時だけお知らせします。
        </p>
      )}
      {groups.map(([label, list]) => list.length > 0 && (
        <div key={label}>
          <p style={{ margin: '8px 0 2px', fontSize: 10, color: 'var(--text-faint)' }}>{label}</p>
          {list.map((n) => (
            <div key={n.id} style={{ borderTop: '1px solid var(--line)', padding: '6px 0' }}>
              <p style={{ margin: 0, fontSize: 12 }}>
                <b style={{ color: SEV_TONE[n.severity], border: `1px solid ${SEV_TONE[n.severity]}`,
                            borderRadius: 4, padding: '0 4px', fontSize: 9.5 }}>{SEV_JA[n.severity]}</b>
                <b style={{ marginLeft: 6 }}>{n.titleJa}</b>
                <span style={{ marginLeft: 6, fontSize: 9.5, color: 'var(--text-faint)' }}>
                  {jstStamp(n.createdAt).time}{n.occurrenceCount > 1 ? ` · ${n.occurrenceCount} updates` : ''}
                </span>
              </p>
              <p style={{ margin: '2px 0 0', fontSize: 11, color: 'var(--text-sub)', lineHeight: 1.6 }}>{n.bodyJa}</p>
              <p style={{ margin: '1px 0 0', fontSize: 10, color: 'var(--text-faint)' }}>次に確認: {n.checkNextJa}</p>
              <button type="button"
                      onClick={() => { n.notificationIds.forEach(dismissNotification); bump(); }}
                      style={{ marginTop: 2, fontSize: 9.5, cursor: 'pointer', background: 'transparent',
                               color: 'var(--text-faint)', border: '1px solid var(--line)',
                               borderRadius: 5, padding: '0 6px' }}>閉じる</button>
              {n.isPrivate && <span style={{ marginLeft: 6, fontSize: 9, color: 'var(--text-faint)' }}>端末内のみ</span>}
            </div>
          ))}
        </div>
      ))}
      <p style={{ margin: '8px 0 0', fontSize: 9, color: 'var(--text-faint)' }}>
        設定でPrimary Action、カタリスト、リスク、権限、復元の端末内通知を個別に選べます。サーバー送信や外部push/メールはありません。
      </p>
    </section>
  );
};

export default NotificationPanel;
