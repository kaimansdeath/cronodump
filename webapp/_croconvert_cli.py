"""
Обёртка для запуска crodump.croconvert в подпроцессе.

Зачем: библиотека печатает диагностику (Warning: ..., ERROR decoding ...)
обычным print() в stdout. Для шаблонов html/postgres stdout — это сам
результат, поэтому предупреждения попадали внутрь .html и .sql
(в SQL-дампе строка "Warning: ..." перед CREATE TABLE ломает импорт).

Здесь builtins.print перенаправляется в stderr. На вывод шаблона это
не влияет: jinja пишет через ссылку sys.stdout, полученную при импорте.
"""
import builtins
import sys

_real_print = builtins.print


def _print_to_stderr(*args, **kwargs):
    kwargs["file"] = sys.stderr
    _real_print(*args, **kwargs)


builtins.print = _print_to_stderr

from crodump.croconvert import main  # noqa: E402

if __name__ == "__main__":
    main()
