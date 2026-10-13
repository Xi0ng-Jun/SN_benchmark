// View-state types shared by the Agent chat hooks and components.

export type SessionLinkStatus = 'idle' | 'linking' | 'linked' | 'error';

export type SessionRemoteStatus = 'unselected' | 'loading' | 'available' | 'missing' | 'unavailable';

export type SessionRemote = {
  status: SessionRemoteStatus;
  detail?: string;
  observedAtLabel?: string;
  agentStatusLabel?: string;
  agentTitle?: string | null;
  agentProvider?: string;
  agentModel?: string | null;
};

export type SessionEntryKind = 'user' | 'assistant' | 'activity';

export type SessionEntry = {
  id: string;
  label: string;
  kind: SessionEntryKind;
  text: string | null;
  truncated: boolean;
  activityType?: string;
  candidateBlockedReason: string | null;
};

export type SessionPageStatus = 'idle' | 'loading' | 'ready' | 'error';

export type SessionPage = {
  status: SessionPageStatus;
  freshness: 'current' | 'stale';
  pendingAction: 'none' | 'refresh' | 'older' | 'newer';
  detail?: string;
  observedAtLabel?: string;
  entries: readonly SessionEntry[];
  hasOlder: boolean;
  hasNewer: boolean;
};

export type MessageStatus = 'idle' | 'sending' | 'accepted' | 'error' | 'unknown';
export type MessageBehavior = 'interrupt' | 'steer';

export type ContextMode = 'reference' | 'path';

export type ContextSnapshotView = {
  id: string;
  title: string;
  text: string;
  sha256: string;
  complete: boolean;
};

export type ContextPreview = {
  status: 'idle' | 'loading' | 'ready' | 'error';
  current: boolean;
  snapshot: ContextSnapshotView | null;
  detail?: string;
};
