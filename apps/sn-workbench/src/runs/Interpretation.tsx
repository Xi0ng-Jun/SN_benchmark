import React from 'react';
import { Modal, Pressable, ScrollView, Text, View } from 'react-native';
import { X } from 'lucide-react-native';
import { Copy, s, useTheme } from '../ui';
import { IconButton } from '../shell';

// The single place for reading rules. Pages show facts and states; the
// methodology behind them lives here instead of under every number.
const sections: { title: string; items: string[] }[] = [
  { title: '分数', items: [
    '官方分来自独立评分动作保存的 scores.json；页面原样显示汇总与分母，不重算均值，不合成跨套件总分。',
    '分数只负责发现问题与确认未退化；改什么由失败归因的证据链决定，不由均分决定。',
    'n=1 或显式子集（scope=subset）的成绩只证明链路可用，不是 benchmark 成绩或排名。',
    'batch-only 指标只有批量值，没有逐题值；pending 指标是评分缺口，不补零。',
  ] },
  { title: '状态与分母', items: [
    'success、no_answer、clarification、error、missing、pending 各自保留；缺分不伪装成有效零分，不按低分重跑或只选成功题。',
    '有效零分是调查线索，不是人工确认的错误。',
    '“待读”= 生成状态不是 success，或官方主指标严格低于服务端配置的阈值（QASPER answer_f1 默认 < 0.5；其他套件以 run 卡上显示的规则为准）。缺分保持未知，不按 0 计入。',
    '待读是读案例的入口，不是人工决定，也不是失败分类；侧栏的待决数只统计真实保存的 undecided 记录（候选与人评）。',
    '案例人工判读暂记入失败账本 docs/failure-ledger.md，由统筹写入；工作台本页不保存判断。',
    '“变差／变好”只在两个 run 之间存在；单个 run 没有基线时只显示待读题。',
  ] },
  { title: '阶段指标（诊断）', items: [
    '证据召回、上下文精度、引用精度、拒答率从已保存的检索上下文、引用锚点与输出状态离线计算，标为 diagnostic，不是官方证据分。',
    '证据召回按 gold 证据全文包含判定，部分段落不计入；引用精度只看锚点是否含 gold 单元，不是蕴含判定。',
    '调用数分两路观测：provider 日志与 scheduled journal，二者不相加；journal 恒为 history_exhaustive=false，不证明完整执行历史。',
  ] },
  { title: '案例与证据高亮', items: [
    'gold 只用于分析与诊断；生成进程从不接收 gold。gold 不穷举所有有效证据。',
    '高亮按空白折叠后的原文精确匹配定位：黄色为 gold 证据，蓝色为答案引用的对象，灰色为检索到但未引用的块。匹配不到的片段单独列出，不做模糊匹配。',
    '证据、引用和检索排名只来自 run 实际保存的输出；未记录不代表未发生。',
  ] },
  { title: '比较与翻转', items: [
    '翻转表先核对 bundle、题单与评分身份，再逐题对比官方主指标。未准入时分别阅读；同一次运行的自比较只用于回访。',
    '正式比较须核对 bundle、范围、case IDs、scorer 与实际分母；作者复现、适配方法与公开答卷分别标识，不把端到端分差直接归因于某模块。',
    '结果比较只读既有结果，不重新评分；左右列保留各自官方批次原值与实际分母。逐题均值差是独立估计，不替代官方批次值；条件差异不代表模块因果。',
  ] },
  { title: 'Agent 会话', items: [
    '会话关联只保存任务内的本地关系；远程状态以显式只读读取为准，可读取不等于正在运行或已经完成。',
    'SDK 返回发送 ACK 只表示消息被接收，不代表执行完成。',
  ] },
  { title: '身份与 provenance', items: [
    '源码身份来自实际捕获并加载的字节（sn_source_ref、head_revision、dirty 清单）；文件 hash 不等于完整执行环境。',
    '配置以 hash 表示；远端模型权重无法由 hash 证明，服务代际（generation）只是启动时观测到的绑定。',
    '有效特性集合尚未投影进 run 身份（runner 关闭了多个 SN 模块），结论不能读成“完整 SN 的成绩”。',
    '实验配置补了部署配置缺失的 chat binding；结果是实验配置下的成绩。',
  ] },
  { title: '更改与验证', items: [
    '更改页只读 git diff，相对 Task 绑定的 base；不提供提交、合并或回滚。',
    '没有起点快照时，“起点既有 dirty”与“之后改动”无法区分，显示为未知。',
    '“验证”在 R1 阶段只生成可复制命令；运行会调用模型并产生费用，必须先由人确认预算卡。Job 可执行（M4）后才变为按钮。',
  ] },
  { title: '计划与记录', items: [
    '计划草稿保存的是选题意图：源码未捕获，模型服务与 scorer 未选择，execution=false；保存不运行评测。',
    '随机抽样按 seed 确定性生成，同 seed 同输入得到同一选题；抽样结果仍需人工核对。',
    '人工判断与笔记独立保存，不覆盖分数；登记时间不代替实验时间。',
    '接入已有结果只核验并绑定冻结批次，不启动模型；原工件只读。',
  ] },
];

export function InterpretationButton({ onPress }: { onPress: () => void }) {
  const theme = useTheme();
  return <Pressable accessibilityRole="button" accessibilityLabel="如何解读" onPress={onPress} testID="open-interpretation"
    style={({ pressed }) => ({ height: 28, paddingHorizontal: 8, borderRadius: 6, flexDirection: 'row', alignItems: 'center', gap: 6,
      backgroundColor: pressed ? theme.selected : 'transparent' })}>
    <View style={{ width: 14, height: 14, borderRadius: 7, borderWidth: 1, borderColor: theme.muted, alignItems: 'center', justifyContent: 'center' }}>
      <Text style={{ fontSize: 9, lineHeight: 10, color: theme.muted, fontWeight: '600' }}>i</Text>
    </View>
    <Text style={{ fontSize: 12, color: theme.muted }}>如何解读</Text>
  </Pressable>;
}

export function Interpretation({ visible, onClose }: { visible: boolean; onClose: () => void }) {
  const theme = useTheme();
  return <Modal visible={visible} transparent animationType="fade" onRequestClose={onClose}>
    <View style={{ flex: 1, backgroundColor: '#0007', alignItems: 'center', justifyContent: 'center', padding: 24 }}>
      <View testID="interpretation" style={{ backgroundColor: theme.bg, width: '100%', maxWidth: 640, maxHeight: '90%', borderRadius: 12, borderWidth: 1, borderColor: theme.line }}>
        <View style={{ height: 44, paddingHorizontal: 16, flexDirection: 'row', alignItems: 'center', borderBottomWidth: 1, borderColor: theme.line }}>
          <Text style={{ flex: 1, fontSize: 14, fontWeight: '600', color: theme.text }}>如何解读工作台中的数字</Text>
          <IconButton label="关闭" icon={X} onPress={onClose} />
        </View>
        <ScrollView contentContainerStyle={{ padding: 20, gap: 18 }}>
          {sections.map(section => <View key={section.title} style={{ gap: 6 }}>
            <Text style={[s.section, { color: theme.text }]}>{section.title}</Text>
            {section.items.map(item => <View key={item} style={{ flexDirection: 'row', gap: 8 }}>
              <Text style={{ color: theme.muted, fontSize: 12, lineHeight: 20 }}>·</Text>
              <Copy small style={{ flex: 1 }}>{item}</Copy>
            </View>)}
          </View>)}
        </ScrollView>
      </View>
    </View>
  </Modal>;
}
