import React from 'react';
import { ImportantEventsCard } from '../components/dashboard/ImportantEventsCard';
import { NotificationPanel } from '../components/NotificationPanel';
import { NewsAlertsPanel } from '../components/notifications/NewsAlertsPanel';
import { PageShell } from './PageShell';

// v13.5.60 (owner iPhone review 2026-09-07): the third page is organised as
// three named sections — ニュース・市場リスク / 銘柄・判断の変化 / イベント —
// each with a stable anchor so a tap on Today lands on the matching section.
export const ALERTS_SECTION_IDS = {
  news: 'news-intel',
  assets: 'asset-alerts',
  events: 'important-events',
} as const;

export const NotificationsPage: React.FC = () => (
  <PageShell
    title="Notifications"
    subtitle="重大ニュース・市場リスク、銘柄と判断の変化、経済イベントを確認します。"
  >
    <NewsAlertsPanel />
    <NotificationPanel />
    <ImportantEventsCard />
  </PageShell>
);

export default NotificationsPage;
