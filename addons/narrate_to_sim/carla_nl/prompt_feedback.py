"""Actionable input guidance shared by the reader and Add Scenario."""
import re


STATE_INTERVAL_HINT = ('What happens after this state interval? Specify the initial lane/speed at 0s, then describe the intended timed action with its start and duration. '
                       'The replacement below describes only the initial state; it does not include this interval or a following action. Apply only if that matches your intent. / 這段狀態時間結束後要發生什麼？下方替換句僅描述初始狀態，不包含時間限制或後續動作；請確認符合原意再套用。')


def replacement_omission_notice(text):
    return (f'These words are not included in the replacement: "{text}". '
            'That requirement is not simulated. Apply only if this matches your intent; '
            'otherwise revise the original description. Ctrl+Z restores the original.')


class PromptInputError(ValueError):
    """An expected authoring problem; the prompt can be edited and retried."""

    def __init__(self, message, *, clause='', reason='', code='', unrecognized='', example='', recognized=(), source_occurrence=0):
        super().__init__(message)
        self.clause = clause
        self.reason = reason
        self.code = code
        self.unrecognized = unrecognized
        self.example = example
        self.recognized = list(recognized)
        self.source_occurrence = source_occurrence


def source_clause_at(text, position):
    """Retain the instruction owning a lexical error before normalization."""
    boundaries = list(re.finditer(r'[.!?](?=\s|$)|[。！？;\r\n]+', text))
    start = max((match.end() for match in boundaries if match.end() <= position), default=0)
    end = min((match.start() for match in boundaries if match.start() >= position), default=len(text))
    return text[start:end].strip()


PLACEHOLDER_HINTS = {
        'lane': 'left / middle / right', 'destination': 'left / middle / right',
        'side': 'left / right', 'ahead of/behind': 'ahead of / behind',
        'speed-unit': 'km/h / mph / m/s', 'position-unit': 'm / s',
        'speed-action': 'accelerates / brakes; choose the separate speed action',
        'motion-mode': 'speed / time_gap / fixed_speed; choose one movement mode',
        'duration': 'positive duration in seconds, e.g. 3 / 正數用時（秒），例如 3',
        'start': 'start time in seconds, e.g. 10 / 開始秒數，例如 10',
        'end': 'finish time in seconds / 完成秒數', 'reference': "ego's position reference time in seconds / ego 位置參考秒數",
        'time-unit': 's / sec / seconds', 'time': 'time in seconds / 秒數',
        'speed': 'speed value; retain the following unit / 速度數值，保留後方單位',
        'converted-speed': 'convert the original speed to km/h / 將原速度換算成 km/h 的數值',
        'target': 'target speed value / 目標速度數值', 'source': 'starting speed value / 起始速度數值',
        'gap': 'gap value; retain its unit and direction / 車距數值，保留單位及前後方向',
        'distance': 'distance value; retain its unit and direction / 距離數值，保留單位及前後方向',
        'number': 'vehicle or object number; replace <number> with a whole number, e.g. 3 / 車輛或物件編號，例如 3', 'type': 'cone / barrier / barrel / box / disabled vehicle',
        'rate': 'positive acceleration/deceleration magnitude in m/s2 / 正數加減速度大小（m/s2）',
        'total': 'total scenario seconds / 場景總秒數',
        'vehicle': 'a vehicle already in this scene: ego / v1 to v6; define the vehicle first',
        'reference vehicle': 'ego or a defined vehicle, e.g. vehicle_1; distance is measured at obstacle appearance',
        'change': 'speed change amount; retain the unit and the word by',
        'cue': 'beep / speech / circle / visual',
        'alert-mode': 'Visual / Auditory Beep / Auditory Speech / Visual with Beep or Speech',
        'audio': 'beep / speech',
        'ttc': 'positive seconds before predicted collision, e.g. 7',
        'obstacle': 'a defined obstacle, e.g. obstacle 1',
        'text': 'speech message in double quotes, e.g. "Please take over."',
}


def prompt_placeholder_fields(prompt):
    """Shared field ranges for diagnostics and one-click editor selection."""
    # A quoted cue is literal speech payload, including any angle brackets.
    masked = re.sub(r'"(?:\\.|[^"\\])*"', lambda m: ' ' * len(m[0]), prompt)
    return [match for match in re.finditer(r'<([A-Za-z][\w /-]*)>', masked)
            if match[1].lower() in PLACEHOLDER_HINTS]


def check_unfilled_placeholders(prompt):
    """Template fields are authoring blanks, not unsupported driving words."""
    fields = prompt_placeholder_fields(prompt)
    if not fields:
        return
    lines = ['Please provide missing details / 請補充：',
             '\nUnfilled template fields / 尚未填寫的範例欄位：',
             '  Replace the entire <...> with your value; keep the unit outside it. / 將整個 <...> 換成你的值，保留括號外的單位。']
    for field in fields:
        line = prompt.count('\n', 0, field.start()) + 1
        lines.append(f'  • Line {line} / 第 {line} 行 · {field[0]}：{PLACEHOLDER_HINTS[field[1].lower()]}')
    lines.extend(['\nNext step / 下一步：',
                  '  • Fill these fields directly in the left editor, then click Analyze. / 直接填寫左側待填欄位，再按 Analyze；不需要再替換原句。'])
    error = PromptInputError('\n'.join(lines), code='placeholders')
    error.missing_count = len(fields)
    error.placeholder_fields = [(field.start(), field.end()) for field in fields]
    raise error


def missing_information(issues):
    source_issues = list(issues)
    grouped = {}
    for issue in source_issues:
        role, fields, example = issue[:3]
        entry = grouped.setdefault(role, [role, [], [], [], []])
        # Equal field names on different actions are distinct missing facts.
        # Initial-state deduplication belongs to the parser's actor tracking.
        clause = issue[4] if len(issue) > 4 else ''
        entry[1].extend((field, clause) for field in dict.fromkeys(fields))
        if example and example not in entry[2]:
            entry[2].append(example)
        if len(issue) > 3:
            entry[3].extend(value for value in issue[3] if value not in entry[3])
        if len(issue) > 4 and issue[4] and issue[4] not in entry[4]:
            entry[4].append(issue[4])
    issues = [(role, fields, '\n  '.join(examples), known, clauses)
              for role, fields, examples, known, clauses in grouped.values()]
    lines = ["Please provide missing details / 請補充："]
    count = 0
    for issue in issues:
        role, fields, example = issue[:3]
        lines.append(f"\n{role}:")
        clauses = issue[4]
        lines.extend('  Description / 待修改描述' + (f' [{i}]' if len(clauses) > 1 else '') + '：' + clause
                     for i, clause in enumerate(clauses, 1))
        unique_fields = fields
        count += len(unique_fields)
        lines.extend('  • ' + (f'[{clauses.index(clause) + 1}] ' if len(clauses) > 1 and clause in clauses else '') + field
                     for field, clause in unique_fields)
    lines.append("\nExample structure / 填寫範例（替換 <...>）：")
    for role, _fields, example, *_rest in issues:
        lines.append(f"\n{role}:\n  {example}")
    recognized = [issue for issue in issues if issue[3]]
    if recognized:
        lines.append("\nRecognized / 已辨識（無需重填）：")
        for issue in recognized:
            lines.append(f"{issue[0]}:")
            lines.extend('  • ' + value for value in issue[3])
    lines.append("\nNext step / 下一步：\n  • Replace only the affected description; keep the other vehicles, objects and actions. Fill <...>, then click Analyze. / 只修改對應原句，保留其他車輛與動作；補齊 <...> 後按 Analyze，避免重複新增同一車號。")
    error = PromptInputError("\n".join(lines))
    error.missing_count = count
    # Keep clause/example associations separate from the grouped display text.
    # Editors must not guess a source clause from an actor label alone.
    error.repair_requests = [dict(role=issue[0], example=issue[2],
                                  clause=issue[4] if len(issue) > 4 else '',
                                  notice=issue[5] if len(issue) > 5 else '',
                                  fields=list(issue[1]),
                                  recognized=list(issue[3]) if len(issue) > 3 else [])
                             for issue in source_issues if issue[2]]
    return error


def format_prompt_problem(prompt, error, *, repair_requests=None):
    if getattr(error, 'missing_count', 0):
        return str(error)
    reason = getattr(error, 'reason', '') or str(error)
    reason = reason.split('Use explicit actor, lane, speed and event times')[0].strip()
    clause = getattr(error, 'clause', '') or prompt.strip()
    unknown = getattr(error, 'unrecognized', '')
    code = getattr(error, 'code', '')
    example = getattr(error, 'example', '')
    recognized = getattr(error, 'recognized', ())
    # Classify the diagnostic, not every word in the user's sentence. A state
    # containing 'speed' must not be misdiagnosed as a braking problem.
    lower = reason.splitlines()[0].lower() if reason else ''
    context = clause.lower()
    state = 'Ego in the <lane> lane at <speed> km/h.'
    vehicle = 'vehicle_<number> in the <lane> lane at <speed> km/h <gap> seconds <ahead of/behind> ego.'
    event = 'At <start>s vehicle_<number> changes into the <destination> lane over <duration>s.'
    hint = ('Use a supported state or action form below. Confirm that it preserves your intended meaning; '
            'unrecognized words are not included in the example. / 請依下方格式改寫，並確認符合原意；範例未包含尚未辨識的含義。')
    from .prompt_recovery import recovery_wording
    fallback = recovery_wording(clause)
    entity_example = ''
    if re.search(r'\b(?:obstacle[ _-]*\d*|cone|barrier|barrel|debris|crate|box)\b', clause, re.I):
        entity_example = 'Obstacle <number>: a <type> in the <lane> lane <distance> metres <ahead of/behind> <reference vehicle> at <start>s until <end>s.'
    elif re.search(r'\b(?:ped(?:estrian)?|person|walker)(?:[ _#-]*\d+)?\b', clause, re.I):
        entity_example = 'Pedestrian <number> crossing from <side> to <side> <distance> metres <ahead of/behind> ego at <start>s over <duration>s.'
    if code in ('state', 'event', 'entity') and unknown:
        reason = ('Some words in this description were not understood. / 這段描述有尚未辨識的字詞，請參考下方改寫範例。')
    if any(word in lower or word in context for word in ('ttc', 'traffic light', 'red light')) or re.search(r'\b(?:if|unless|when)\b', context) or 'condition' in lower:
        hint = ('This conditional trigger is not supported in ordinary prose. Use a timed alternative only if it matches your intent; '
                'otherwise configure a supported trigger in scenario settings. / 此條件觸發尚未支援口語輸入；只有符合原意時才改成指定時間，否則請使用場景設定支援的觸發方式。')
        fallback = event
    elif re.search(r"\b(?:not|never|might|may|probably|possibly|won't|can't|shouldn't)\b", context):
        hint = ('Specify the definite state or action you intend. Do not simply delete a negation or uncertainty word; '
                'confirm the intended lane/speed/action, or author separate alternatives. / 請先確認實際要發生的狀態或動作；不要直接刪除否定詞，可將不同可能性拆成獨立場景。')
    elif 'position reference time' in lower and 'scenario duration' in lower:
        hint = ('Extend the scenario to include the reference time, or choose an ego position reference time within the scenario. '
                'Keep the separate appearance time unchanged unless you also intend to change it. / 請延長場景至位置參考時間，或修改參考秒數至場景範圍內；出現時間是另一個欄位，無須一起修改。')
        fallback = 'Create a <total>-second scenario.\n' + (entity_example or vehicle)
        fallback = fallback.replace('<distance> metres <ahead of/behind> ego', "at ego's position at <reference>s")
        fallback = fallback.replace('<distance> metres <ahead of/behind> <reference vehicle>', "at ego's position at <reference>s")
        fallback = fallback.replace('<gap> seconds <ahead of/behind> ego', "at ego's position at <reference>s")
        entity_example = ''
    elif 'position reference' in lower or 'relative to ego' in lower:
        hint = ('Specify the position relative to ego or the initial ego position. Recalculate the amount from that reference; '
                'do not replace another vehicle or object with ego while keeping an unrelated gap. / 請換算成相對 ego 或 ego 初始位置的距離，再填入下方格式。')
        fallback = vehicle
    elif 'duplicate' in lower:
        hint = 'Edit the existing definition for that number, or use a different unused number for a new vehicle or object. / 同一車號只能有一個初始狀態；請修改原定義，或為另一個對象使用未占用的編號。'
        fallback = vehicle
    elif 'spawn' in lower:
        hint = ('Vehicles currently start at 0s. If that matches your intent, define the initial state, then describe its later action separately. '
                'A delayed vehicle appearance needs runner support. / 車輛目前從第 0 秒存在；若符合原意，請分開寫初始狀態與後續動作，延遲生成仍需執行器支援。')
        fallback = vehicle + '\n' + event
    elif any(word in lower or word in context for word in ('weather', 'rain', 'fog')):
        hint = 'Choose the Weather preset in Add Scenario; keep the traffic description below. / 請在 Add Scenario 的 Weather 選擇天氣，文字中保留交通場景描述。'
        fallback = state
    elif code == 'timing_reference':
        hint = 'Put the duration or finish time directly in the sentence for the named action. Confirm which action it belongs to; a braking duration cannot supply a lane-change duration. / 請把用時或完成時間寫在對應動作的同一句；煞車用時不能當作變道用時。'
        fallback = event + '\nAt <start>s vehicle_<number> brakes to <target> km/h over <duration>s.'
    elif code == 'state_interval':
        hint = STATE_INTERVAL_HINT
    elif lower.startswith('circle target'):
        hint = 'Choose a defined vehicle or obstacle as the circle target. If it has no initial description yet, add that description first.'
    elif code == 'entity_timing':
        hint = 'Keep one start time. If both duration and finish time are supplied, finish = start + duration. Confirm which value to change before filling the other missing fields. / 請保留一個開始時間；同時指定用時與完成時間時，完成時間 = 開始時間 + 用時。先確認要修改哪個時間，再補其他缺項。'
    elif code == 'entity_conflict':
        hint = 'Confirm one consistent description for this object before filling missing values: one obstacle type/lane, or one pedestrian crossing direction. / 請先確認此對象的一致描述，再補其他缺項：障礙物指定單一種類／車道，行人指定單一橫穿方向。'
    elif code in ('state', 'event', 'entity'):
        fallback = state if code == 'state' else event
    elif any(word in lower for word in ('undefined', 'ambiguous vehicle', 'ambiguous reference', 'subject')):
        hint = 'Name vehicle 1 to vehicle 6 and define its initial lane, speed and gap before referring to it. / 請明確指定車號，並提供該車的初始車道、速度及車距。'
        fallback = vehicle + '\n' + event
    elif 'overlap' in lower:
        hint = 'Start the later action after the earlier one finishes, or revise the durations. / 請將後一個動作移至前一個完成後，或修改持續時間。'
        fallback = event
    elif any(word in lower for word in ('beyond scenario', 'exceed', 'outside scenario')):
        hint = 'Extend the scenario duration through the event end, or move/shorten the event. / 請延長場景至動作完成，或提前／縮短動作。'
        fallback = 'Create a <total>-second scenario.\n' + event
    elif 'pedestrian' in lower and 'speed' in lower:
        hint = 'Specify the crossing duration, direction, start and position. Walking speed cannot be converted to crossing duration without path geometry. / 請指定行人橫穿用時、方向、開始時間及位置；未確定橫穿路徑前，不能由步行速度推算用時。'
    elif 'initial speed' in lower or 'one speed and one initial lane' in lower:
        hint = 'Give one initial lane and one initial speed with units (0 means stationary). / 請填一個初始車道與帶單位的初始速度（0 表示靜止）。'
        fallback = state
    elif 'source speed' in lower:
        hint = 'The stated starting speed must match the initial speed and all preceding actions. Correct the from-speed or the earlier speed plan; preserve the target and timing if they are intended. / from 的起始速度須符合初始速度與先前動作；請修改起始速度或先前速度計畫。'
        fallback = 'At <start>s vehicle_<number> brakes from <source> km/h to <target> km/h over <duration>s.'
    elif 'speed' in lower:
        hint = 'Check starting/target speeds and units. Braking must reduce speed; acceleration must increase it. Provide duration or rate. / 請核對起始與目標速度、單位及用時；煞車應減速，加速應增速。'
        fallback = 'At <start>s vehicle_<number> brakes to <target> km/h over <duration>s.'
    elif 'lane' in lower:
        hint = 'Choose one reachable destination lane: left, middle or right, considering earlier lane changes. / 請依目前車道與先前變道，指定可到達的單一目標車道。'
        fallback = event
    elif any(word in lower for word in ('duration', 'time')):
        hint = 'Specify one start and one positive duration or completion time, with units. / 請指定一個開始時間及正數用時，或完成時間，並附上單位。'
        fallback = event
    lines = ['Please revise this part of the scenario / 請修改以下場景描述：',
             '\nProblem clause / 問題片段：', '  ' + clause,
             '\nWhy / 原因：', '  • ' + reason]
    if unknown:
        lines += ['\nUnrecognized / 未辨識的字詞：', '  • ' + unknown]
    lines += ['\nNext step / 怎麼改：', '  • ' + hint]
    lines += ['\nExample structure / 改寫範例（替換 <...>，確認符合原意）：']
    suggested = example or entity_example or fallback
    lines += ['  ' + line for line in suggested.splitlines()]
    if repair_requests is not None:
        repair_requests.append(dict(role='description', example=suggested, clause=clause,
                                    source_occurrence=getattr(error, 'source_occurrence', 0),
                                    notice=replacement_omission_notice(unknown) if unknown else
                                    'Review the template before Analyze; it may change the original meaning. Ctrl+Z restores the original.'))
    if recognized:
        lines += ['\nRecognized / 已辨識（無需重填）：'] + ['  • ' + item for item in recognized]
    lines += ['\nNext step / 修改後：', '  • Edit the original prompt and click Analyze. / 原文已保留，修改後按 Analyze 再次分析。']
    return '\n'.join(lines)
