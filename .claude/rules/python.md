---
paths:
  - "**/*.py"
---

# Python конвенции

- Type hints для всех публичных функций
- Используй `pathlib.Path` вместо `os.path`
- f-strings вместо `.format()` и `%`
- `dataclass` или `pydantic` вместо обычных dict
- Логирование через `logging`, не `print()`
- Асинхронный код через `asyncio`, не `threading`
