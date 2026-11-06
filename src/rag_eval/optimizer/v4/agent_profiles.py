"""Resolve adapter role defaults and preflight the creation-store authorization."""
from .config import ADAPTER_DEFAULTS
from rag_eval.workbench.agent_creations import CreationProfileUnavailable

ROLE_LABELS = dict(analyst='分析', implementer='实现', reviewer='审查', calibrator='校准顾问')


def resolve(settings, require, roles=tuple(ROLE_LABELS)):
    values = dict(ADAPTER_DEFAULTS, **(settings or {}))
    for role in ('reviewer', 'calibrator'):
        values[role + '_profile'] = values[role + '_profile'] or values['analyst_profile']
    for role in roles:
        try:
            require(values[role + '_profile'])
        except CreationProfileUnavailable:
            inherited = '（未指定时继承 analyst_profile）' if role in ('reviewer', 'calibrator') else ''
            raise CreationProfileUnavailable(
                f'{ROLE_LABELS[role]} Agent 的模型配置不可用于当前工作区；'
                f'请在“费用与 Agent 设置”中选择已登记配置，或设置 optimizer_v4.adapter.{role}_profile{inherited}') from None
    return {role + '_profile': values[role + '_profile'] for role in roles}


def binding_digest(binding, profiles):
    from .calibration_session import canonical_hash
    return canonical_hash(dict(workspaceId=binding['workspaceId'],
        profiles={value: binding['profiles'][value] for value in profiles.values()}))


def preflight(adapter, task_id, settings, roles=tuple(ROLE_LABELS), *, expected=None):
    store = adapter._round_ops.creations.store
    profiles = resolve(settings, lambda profile: store.require_profile(task_id, profile), roles)
    if expected is not None and binding_digest(adapter._config['agent_creation'], profiles) != expected:
        raise CreationProfileUnavailable('服务器 Agent 模型配置已改变；请重启工作台加载当前配置后重新预览确认')
    return profiles
