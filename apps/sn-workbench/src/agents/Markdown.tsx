import React from 'react';
import { Linking, ScrollView, Text, View } from 'react-native';
import { s, useTheme } from '../ui';

// Agent replies are Markdown. A small renderer for what Agents actually write
// (headings, lists, code, quotes, tables, emphasis, links); anything else stays text.
type Block =
  | { kind: 'code'; text: string }
  | { kind: 'heading'; level: number; text: string }
  | { kind: 'rule' }
  | { kind: 'quote'; text: string }
  | { kind: 'table'; head: string[]; rows: string[][] }
  | { kind: 'item'; depth: number; marker: string; text: string }
  | { kind: 'para'; text: string };

const cells = (line: string) => line.trim().replace(/^\||\|$/g, '').split('|').map(cell => cell.trim());
const isRule = (line: string) => /^\s*([-*_])(\s*\1){2,}\s*$/.test(line);
const tableSeparator = (line?: string) => line !== undefined && /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(line);
const listItem = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/;

function blocks(source: string): Block[] {
  const lines = source.replace(/\r\n?/g, '\n').split('\n');
  const out: Block[] = [];
  for (let i = 0; i < lines.length;) {
    const line = lines[i];
    const fence = /^\s*(```|~~~)/.exec(line);
    if (fence) {
      const body: string[] = [];
      for (i++; i < lines.length && !lines[i].trim().startsWith(fence[1]); i++) body.push(lines[i]);
      out.push({ kind: 'code', text: body.join('\n') });
      i++;
      continue;
    }
    if (!line.trim()) { i++; continue; }
    const heading = /^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$/.exec(line);
    if (heading) { out.push({ kind: 'heading', level: heading[1].length, text: heading[2] }); i++; continue; }
    if (isRule(line)) { out.push({ kind: 'rule' }); i++; continue; }
    if (/^\s*>/.test(line)) {
      const body: string[] = [];
      for (; i < lines.length && /^\s*>/.test(lines[i]); i++) body.push(lines[i].replace(/^\s*>\s?/, ''));
      out.push({ kind: 'quote', text: body.join('\n') });
      continue;
    }
    if (line.includes('|') && tableSeparator(lines[i + 1])) {
      const head = cells(line);
      const rows: string[][] = [];
      for (i += 2; i < lines.length && lines[i].includes('|') && lines[i].trim(); i++) rows.push(cells(lines[i]));
      out.push({ kind: 'table', head, rows });
      continue;
    }
    const item = listItem.exec(line);
    if (item) {
      const body = [item[3]];
      // Continuation lines (indented, not a new item) belong to the item.
      for (i++; i < lines.length && lines[i].trim() && !listItem.test(lines[i]) && /^\s{2,}/.test(lines[i]); i++)
        body.push(lines[i].trim());
      out.push({ kind: 'item', depth: Math.floor(item[1].replace(/\t/g, '  ').length / 2), marker: item[2], text: body.join('\n') });
      continue;
    }
    const body: string[] = [];
    for (; i < lines.length && lines[i].trim() && !/^\s*(```|~~~|#{1,6}\s|>)/.test(lines[i]) && !listItem.test(lines[i])
      && !isRule(lines[i]) && !(lines[i].includes('|') && tableSeparator(lines[i + 1])); i++) body.push(lines[i]);
    out.push({ kind: 'para', text: body.join('\n') });
  }
  return out;
}

const inlinePattern = /(`+)([^`]+?)\1|\*\*(.+?)\*\*|__(.+?)__|(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?!\w)|\[([^\]]+)\]\(([^)\s]+)\)/g;

function Inline({ text }: { text: string }) {
  const theme = useTheme();
  const parts: React.ReactNode[] = [];
  let last = 0;
  for (const match of text.matchAll(inlinePattern)) {
    const at = match.index ?? 0;
    if (at > last) parts.push(text.slice(last, at));
    const key = at;
    if (match[2] !== undefined)
      parts.push(<Text key={key} style={[s.mono, { fontSize: 13, backgroundColor: theme.surface, color: theme.secondary }]}>{match[2]}</Text>);
    else if (match[3] !== undefined || match[4] !== undefined)
      parts.push(<Text key={key} style={{ fontWeight: '600' }}><Inline text={match[3] ?? match[4]} /></Text>);
    else if (match[5] !== undefined)
      parts.push(<Text key={key} style={{ fontStyle: 'italic' }}><Inline text={match[5]} /></Text>);
    else
      parts.push(<Text key={key} style={{ color: theme.primary }} onPress={() => { void Linking.openURL(match[7]); }}>{match[6]}</Text>);
    last = at + match[0].length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return <>{parts}</>;
}

export function Markdown({ text }: { text: string }) {
  const theme = useTheme();
  const body = { fontSize: 14, lineHeight: 22, color: theme.text };
  return <View style={{ gap: 8 }}>
    {blocks(text).map((block, i) => {
      switch (block.kind) {
        case 'code':
          return <ScrollView key={i} horizontal style={{ borderWidth: 1, borderColor: theme.line, borderRadius: 6, backgroundColor: theme.surface }}
            contentContainerStyle={{ padding: 10 }}>
            <Text selectable style={[s.mono, { color: theme.secondary }]}>{block.text || ' '}</Text>
          </ScrollView>;
        case 'heading':
          return <Text key={i} selectable style={{ ...body, fontWeight: '600', fontSize: block.level <= 1 ? 18 : block.level === 2 ? 16 : 15,
            lineHeight: block.level <= 2 ? 26 : 22, marginTop: i ? 4 : 0 }}><Inline text={block.text} /></Text>;
        case 'rule':
          return <View key={i} style={{ height: 1, backgroundColor: theme.line }} />;
        case 'quote':
          return <View key={i} style={{ borderLeftWidth: 3, borderColor: theme.lineStrong, paddingLeft: 10 }}>
            <Markdown text={block.text} /></View>;
        case 'table':
          return <ScrollView key={i} horizontal>
            <View style={{ borderWidth: 1, borderColor: theme.line, borderRadius: 6 }}>
              {[block.head, ...block.rows].map((row, r) => <View key={r} style={{ flexDirection: 'row', borderTopWidth: r ? 1 : 0, borderColor: theme.line,
                backgroundColor: r ? undefined : theme.surface }}>
                {block.head.map((_, c) => <Text key={c} selectable style={{ ...body, fontSize: 13, lineHeight: 19, minWidth: 80, maxWidth: 360,
                  paddingHorizontal: 8, paddingVertical: 5, fontWeight: r ? '400' : '600' }}><Inline text={row[c] ?? ''} /></Text>)}
              </View>)}
            </View>
          </ScrollView>;
        case 'item':
          return <View key={i} style={{ flexDirection: 'row', paddingLeft: block.depth * 16, marginTop: -4 }}>
            <Text style={{ ...body, width: /\d/.test(block.marker) ? 26 : 16, color: theme.muted }}>{/\d/.test(block.marker) ? block.marker : '•'}</Text>
            <Text selectable style={{ ...body, flex: 1 }}><Inline text={block.text} /></Text>
          </View>;
        default:
          return <Text key={i} selectable style={body}><Inline text={block.text} /></Text>;
      }
    })}
  </View>;
}
