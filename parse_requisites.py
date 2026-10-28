import re

REQUISITE_FIELDS = ('inn', 'kpp', 'bik', 'settlement_account', 'correspondent_account')

# OCR путает кириллицу с похожей латиницей, поэтому в подписях допускаются оба варианта
INN_KW = r'И[НH][НH]'
KPP_KW = r'[КK]ПП'
BIK_KW = r'БИ[КK]'
# между подписью и значением допускаются «:», «№», переносы строк и слова вроде «покупателя»
GAP = r'[^\d]{0,20}?'

INN_RE = re.compile(rf'{INN_KW}{GAP}(\d{{12}}|\d{{10}})(?!\d)', re.IGNORECASE)
KPP_RE = re.compile(rf'{KPP_KW}{GAP}(\d{{9}})(?!\d)', re.IGNORECASE)
INN_KPP_RE = re.compile(rf'{INN_KW}\s*/\s*{KPP_KW}{GAP}(?:\d{{12}}|\d{{10}})\s*/\s*(\d{{9}})(?!\d)', re.IGNORECASE)
BIK_RE = re.compile(rf'{BIK_KW}{GAP}(\d{{9}})(?!\d)', re.IGNORECASE)

# счёт бывает напечатан группами: 40702 810 9 3800 0012345
ACCOUNT_RE = re.compile(r'(?<!\d)\d(?: ?\d){19}(?!\d)')
# ищется в 40 символах перед номером, берётся ближайшая подпись без цифр между ней и номером
ACCOUNT_KEYWORDS_RE = re.compile(
    r'(?:(?P<corr>[кk]\s?[/.]\s?[сc](?:ч(?:[её]т)?)?|корр?(?:еспондентский|\.|/)?\s*сч(?:[её]т)?)'
    r'|(?P<settlement>[рp]\s?[/.]\s?[сc](?:ч(?:[её]т)?)?|расч(?:[её]тный|\.|/)?\s*сч(?:[её]т)?)'
    r'|(?P<any>\bсч(?:[её]т)?))'
    r'(?=[^\d]*$)',
    re.IGNORECASE,
)


def check_inn(inn: str) -> bool:
    """
    Проверка контрольных цифр ИНН

    Вход:
        inn: ИНН из 10 (одна контрольная цифра) или 12 цифр (две)

    Выход:
        True, если контрольные цифры сходятся
    """
    digits = [int(c) for c in inn]
    weights_sets = {
        10: [[2, 4, 10, 3, 5, 9, 4, 6, 8]],
        12: [[7, 2, 4, 10, 3, 5, 9, 4, 6, 8], [3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8]],
    }
    return all(sum(w * d for w, d in zip(weights, digits)) % 11 % 10 == digits[len(weights)]
               for weights in weights_sets[len(inn)])


def check_account(account: str, bik: str, correspondent: bool) -> bool:
    """
    Проверка контрольного ключа счёта по БИК (алгоритм ЦБ РФ)

    Вход:
        account: номер счёта из 20 цифр
        bik: БИК банка
        correspondent: True для корреспондентского счёта, False для расчётного

    Выход:
        True, если ключ сходится
    """
    # к/с проверяется с префиксом "0" + 5–6 цифры БИК, р/с — с последними 3 цифрами БИК
    number = ('0' + bik[4:6] if correspondent else bik[-3:]) + account
    return sum(int(d) * w for d, w in zip(number, [7, 1, 3] * 8)) % 10 == 0


def parse_requisites(text: str) -> tuple[dict, list]:
    """
    Поиск банковских реквизитов в распознанном тексте

    Вход:
        text: текст страницы после OCR

    Выход:
        (requisites, invalid): уникальные значения по полям REQUISITE_FIELDS в порядке появления
        и список пар (поле, значение), не прошедших проверку контрольной суммы
    """
    text = re.sub(r'(?<=\d)[OoОо]|[OoОо](?=\d)', '0', text)  # OCR читает ноль внутри числа как букву «О»
    found = {
        'inn': INN_RE.findall(text),
        'kpp': KPP_RE.findall(text) + INN_KPP_RE.findall(text),
        'bik': BIK_RE.findall(text),
        'settlement_account': [],
        'correspondent_account': [],
    }

    for match in ACCOUNT_RE.finditer(text):
        account = match.group().replace(' ', '')
        keywords = list(ACCOUNT_KEYWORDS_RE.finditer(text[max(0, match.start() - 40):match.start()]))
        keyword = keywords[-1] if keywords else None
        if keyword and keyword.group('corr'):
            field = 'correspondent_account'
        elif keyword and keyword.group('settlement'):
            field = 'settlement_account'
        elif keyword:
            # «Сч. №» в банковском блоке счёта на оплату стоит и у к/с, и у р/с; к/с в ЦБ всегда начинается с 30101
            field = 'correspondent_account' if account.startswith('30101') else 'settlement_account'
        elif any(check_account(account, bik, True) for bik in found['bik']):
            field = 'correspondent_account'
        elif any(check_account(account, bik, False) for bik in found['bik']):
            field = 'settlement_account'
        else:
            continue  # 20 цифр без подписи и без совпадения ключа с БИК: может быть КБК, УИН и т.п.
        found[field].append(account)

    requisites = {field: list(dict.fromkeys(values)) for field, values in found.items()}
    invalid = [('inn', inn) for inn in requisites['inn'] if not check_inn(inn)]
    if requisites['bik']:
        for field in ('settlement_account', 'correspondent_account'):
            invalid += [(field, account) for account in requisites[field]
                        if not any(check_account(account, bik, field == 'correspondent_account')
                                   for bik in requisites['bik'])]
    return requisites, invalid
