import React, { useEffect, useState } from 'react';
import { Platform, Pressable, Text, View } from 'react-native';
import { ChevronDown, ChevronRight, Copy as CopyIcon, FolderGit2, Lightbulb, Play } from 'lucide-react-native';
import { Button, Copy, fmt, Pill, StatusDot, useTheme, hover } from '../ui';
import { diffCounts, phaseLabels, short, TaskChanges, WorktreeFile } from './contracts';
import { api } from '../contracts';
import { ExecutionPanel } from './ExecutionPanel';

// Association comes only from trusted registration or saved Task provenance; never inferred from HEAD.
const matchLabels: Record<string, string> = {
  explicit_registration: '可信配置显式关联此 Task',
  saved_task_capture: 'run 身份记录了此 Task 的 worktree 捕获',
};

function Header({ title, count, children }: { title: string; count?: string; children?: React.ReactNode }) {
  const theme = useTheme();
  return <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, height: 28 }}>
    <Text style={{ fontSize: 13, fontWeight: '600', color: theme.text }}>{title}</Text>
    {count && <Text style={{ fontSize: 12, color: theme.muted }}>{count}</Text>}
    <View style={{ flex: 1 }} />{children}
  </View>;
}

function Origin({ file }: { file: WorktreeFile }) {
  if (file.preexisting_dirty === null) return <Pill label="起点未知" />;
  if (file.preexisting_dirty && file.changed_since_start) return <Pill label="起点 dirty · 之后又改" tone="warning" />;
  if (file.preexisting_dirty) return <Pill label="起点既有 dirty" />;
  return <Pill label="绑定后改动" tone="running" />;
}

function Patch({ text }: { text: string }) {
  const theme = useTheme();
  return <View style={{ borderTopWidth: 1, borderColor: theme.line }}>
    {text.split('\n').map((line, i) => {
      const add = line.startsWith('+') && !line.startsWith('+++'), del = line.startsWith('-') && !line.startsWith('---');
      const hunk = line.startsWith('@@');
      return <Text key={i} selectable style={{ fontFamily: 'monospace', fontSize: 11, lineHeight: 17, paddingHorizontal: 10,
        color: hunk ? theme.muted : add ? theme.add : del ? theme.del : theme.text, backgroundColor: add ? theme.addBg : del ? theme.delBg : 'transparent' }}>{line || ' '}</Text>;
    })}
  </View>;
}

function FileRow({ file }: { file: WorktreeFile }) {
  const theme = useTheme();
  const [open, setOpen] = useState(false);
  const counts = diffCounts(file.diff);
  const Chevron = open ? ChevronDown : ChevronRight;
  const readable = !!file.diff && !file.private;
  return <View testID="changes-file" style={{ borderBottomWidth: 1, borderColor: theme.line }}>
    <Pressable onPress={() => readable && setOpen(o => !o)} accessibilityRole="button" accessibilityState={{ expanded: open }}
      style={hover(hovered => ({ minHeight: 34, paddingHorizontal: 10, flexDirection: 'row', alignItems: 'center', gap: 8,
        backgroundColor: hovered ? theme.surface : theme.bg }))}>
      <Chevron size={13} color={readable ? theme.muted : 'transparent'} />
      <Text numberOfLines={1} style={{ flex: 1, fontSize: 12, fontFamily: 'monospace', color: theme.text }}>{file.path}</Text>
      <Origin file={file} />
      {file.private ? <Text style={{ fontSize: 11, color: theme.muted }}>私有，仅显示文件名</Text>
        : file.binary ? <Text style={{ fontSize: 11, color: theme.muted }}>二进制</Text>
          : <><Text style={{ fontSize: 12, color: theme.add }}>+{counts.additions}</Text><Text style={{ fontSize: 12, color: theme.del }}>-{counts.deletions}</Text></>}
      {file.truncated && <Text style={{ fontSize: 11, color: theme.warning }}>已截断</Text>}
    </Pressable>
    {open && file.diff && <Patch text={file.diff} />}
  </View>;
}

function CommandBlock({ label, text, testID }: { label: string; text: string; testID: string }) {
  const theme = useTheme();
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    if (Platform.OS === 'web') try { await navigator.clipboard.writeText(text); setCopied(true); setTimeout(() => setCopied(false), 1600); } catch { setCopied(false); }
  };
  return <View testID={testID} style={{ borderWidth: 1, borderColor: theme.line, borderRadius: 8, overflow: 'hidden' }}>
    <View style={{ height: 30, paddingHorizontal: 10, flexDirection: 'row', alignItems: 'center', backgroundColor: theme.surface, borderBottomWidth: 1, borderColor: theme.line }}>
      <Text style={{ flex: 1, fontSize: 11, color: theme.muted }}>{label}</Text>
      <Button label={copied ? '已复制' : '复制'} icon={CopyIcon} onPress={copy} />
    </View>
    <Text selectable style={{ fontFamily: 'monospace', fontSize: 11, lineHeight: 17, padding: 10, color: theme.text }}>{text}</Text>
  </View>;
}

export function ChangesPage({ taskId, changes, error, executionWorktrees, onRefresh, onOpenRun, hypotheses }: { taskId: string; changes: TaskChanges | undefined; error?: string;
  executionWorktrees: { id: string }[]; onRefresh: () => void; onOpenRun: (id: string) => void; hypotheses?: React.ReactNode }) {
  const theme = useTheme();
  const [runError, setRunError] = useState<string | null>(null);
  const [binding, setBinding] = useState(false);
  const bindExecutionWorktree = async () => {
    const worktree = executionWorktrees[0];
    if (!worktree) return;
    setBinding(true); setRunError(null);
    try {
      await api(`/tasks/${encodeURIComponent(taskId)}/worktree`, { worktree_id: worktree.id });
      onRefresh(); setBinding(false);
    } catch (cause) { setRunError(cause instanceof Error ? cause.message : '绑定执行 worktree 失败'); setBinding(false); }
  };
  if (error) return <Copy small style={{ color: theme.error }}>{error}</Copy>;
  if (!changes) return <Copy small muted>正在读取 worktree…</Copy>;
  if (changes.status === 'unbound' || !changes.read) return <View testID="changes-page" style={{ gap: 8 }}>
    <Header title="更改" />
    <Copy small muted>该任务尚未绑定 SN worktree。绑定后可以捕获源码、准备并执行固定评测计划。</Copy>
    {executionWorktrees.length > 0 && <View style={{ gap: 8 }}>
      <Button label={binding ? '正在绑定并捕获…' : `绑定可信执行 worktree · ${executionWorktrees[0].id}`} onPress={bindExecutionWorktree}
        disabled={binding} testID="bind-execution-worktree" />
      {runError && <Text testID="verify-error" style={{ color: theme.error, fontSize: 12 }}>{runError}</Text>}
    </View>}
    <Header title="假设卡" />
    {hypotheses}
  </View>;
  const { read, verification } = changes;
  const b = read.binding;
  return <View testID="changes-page" style={{ gap: 22 }}>
    <View style={{ gap: 8 }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
        <FolderGit2 size={16} strokeWidth={1.6} color={theme.muted} />
        <Text style={{ fontSize: 15, color: theme.text }}>{b.branch ?? read.current_branch ?? 'detached HEAD'}</Text>
        <Text style={{ fontSize: 12, color: theme.muted }}>base {short(b.base_revision, 10)}</Text>
        {read.current_head !== b.base_revision && <Pill label={`HEAD 已移到 ${short(read.current_head, 7)}`} tone="warning" />}
        <View style={{ flex: 1 }} />
        <Pill label={changes.source === 'task_worktree' ? 'Task worktree 绑定' : '临时登记（未持久绑定）'} />
      </View>
      <Text selectable style={{ fontSize: 12, fontFamily: 'monospace', color: theme.muted }}>{b.worktree_path}</Text>
      <Text style={{ fontSize: 12, color: theme.muted }}>
        {b.paseo_workspace_id ? `Paseo workspace ${b.paseo_workspace_id} · ` : ''}起点快照 {b.start_snapshot ? '已记录' : '未记录'} · 当前 HEAD {short(read.current_head, 10)}</Text>
    </View>

    <View style={{ gap: 8 }}>
      <Header title="相对 base 的改动" count={`${read.file_count} 文件`}>
        {read.files.some(f => f.preexisting_dirty === null) && <Text style={{ fontSize: 11, color: theme.muted }}>无起点快照，来源未知</Text>}
      </Header>
      {read.files.length ? <View style={{ borderWidth: 1, borderColor: theme.line, borderRadius: 8, overflow: 'hidden' }}>
        {read.files.map(file => <FileRow key={file.path} file={file} />)}
      </View> : <View testID="changes-empty" style={{ padding: 14, borderWidth: 1, borderStyle: 'dashed', borderColor: theme.line, borderRadius: 8 }}>
        <Text style={{ fontSize: 13, color: theme.text }}>worktree 干净：相对 base 没有已跟踪改动，也没有未跟踪文件。</Text>
      </View>}
      {read.files_truncated && <Text style={{ fontSize: 11, color: theme.warning }}>文件列表已截断</Text>}
    </View>

    <View style={{ gap: 8 }}>
      <Header title="假设卡" />
      {hypotheses}
      {changes.hypotheses.status === 'available' && changes.hypotheses.items.length ? changes.hypotheses.items.map(card =>
        <View key={card.id} testID="changes-hypothesis" style={{ padding: 12, borderWidth: 1, borderColor: theme.line, borderRadius: 8, flexDirection: 'row', gap: 10 }}>
          <Lightbulb size={14} strokeWidth={1.6} color={theme.warning} />
          <View style={{ flex: 1, gap: 4 }}>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
              <Text style={{ fontSize: 12, color: theme.muted, fontVariant: ['tabular-nums'] }}>{card.id}</Text>
              <Text style={{ fontSize: 13, color: theme.text, flexShrink: 1 }}>{card.title}</Text>
              <Pill label={card.status} />
            </View>
            <Text selectable style={{ fontSize: 11, fontFamily: 'monospace', color: theme.muted }}>{card.path}</Text>
          </View>
        </View>) : null}
    </View>

    <View style={{ gap: 8 }}>
      <Header title="此 Task 关联的 run" count={String(changes.runs.length)} />
      {changes.runs.map(run => <Pressable key={run.id} onPress={() => onOpenRun(run.id)} testID="changes-run" accessibilityRole="button"
        style={hover(hovered => ({ paddingVertical: 8, paddingHorizontal: 10, borderWidth: 1, borderColor: theme.line, borderRadius: 8, gap: 4,
          backgroundColor: hovered ? theme.surface : theme.bg }))}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
          <StatusDot tone={!run.state.terminal ? 'running' : run.attention ? 'warning' : 'idle'} />
          <Text numberOfLines={1} style={{ flexShrink: 1, fontSize: 13, color: theme.text }}>{run.suite?.toUpperCase()} · {run.mode} · {run.title}</Text>
          <Text style={{ fontSize: 12, color: theme.muted }}>{phaseLabels[run.state.phase] ?? run.state.phase}{run.attention ? ` · 待读 ${run.attention}` : ''}</Text>
          <View style={{ flex: 1 }} />
          {Object.entries(run.official ?? {}).map(([k, v]) => <Text key={k} style={{ fontSize: 12, color: theme.text, fontVariant: ['tabular-nums'] }}>{k} {fmt(v)}</Text>)}
        </View>
        <Text style={{ fontSize: 11, color: theme.muted, paddingLeft: 14 }}>{matchLabels[run.match] ?? run.match} · ref {short(run.sn_source_ref)}</Text>
      </Pressable>)}
      {!changes.runs.length && <Copy small muted>尚无显式关联此 Task 的 run</Copy>}
    </View>

    {verification && <View testID="changes-verify" style={{ gap: 8 }}>
      <Header title="执行验证" />
      <ExecutionPanel key={taskId} taskId={taskId} onRefresh={onRefresh} />
    </View>}
  </View>;
}
