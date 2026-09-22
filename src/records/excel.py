"""
Excel как источник записей: обёртки над существующими ExcelReader/ExcelWriter.

Поведение Excel-пути не меняется: чтение идёт через тот же `read_sheet()`
(индекс строк не сбрасывается — он же служит write_key), запись — через тот же
`ExcelWriter` с его dirty-флагом.
"""

from typing import Any, Dict, Iterator, List, Optional

from ..excel.reader import ExcelReader
from ..excel.writer import ExcelWriter
from .base import Record, WriteKey


class ExcelRecordSource:
    """Записи одного листа Excel."""

    def __init__(self, reader: ExcelReader, sheet_name: str):
        """
        Args:
            reader: Открытый ExcelReader
            sheet_name: Название листа (= post_type или slug таксономии)
        """
        self.reader = reader
        self.sheet_name = sheet_name
        self.sheet_type = reader.classify_sheet(sheet_name)

    def fields(self) -> List[str]:
        """Колонки листа."""
        return self.reader.read_sheet(self.sheet_name).columns.tolist()

    def read(self) -> Iterator[Record]:
        """Строки листа как записи.

        write_key — label строки pandas: именно по нему `ExcelWriter.update_row`
        находит ту же строку при обратной записи.
        """
        df = self.reader.read_sheet(self.sheet_name)
        is_taxonomy = self.sheet_type == "taxonomy"
        for idx, row in df.iterrows():
            yield Record(
                discriminator=self.sheet_name,
                is_taxonomy=is_taxonomy,
                data=row.to_dict(),
                write_key=idx,
            )


class ExcelRecordWriter:
    """Обратная запись в один лист Excel через общий ExcelWriter.

    `save()` делегируется ExcelWriter'у — файл переписывается только если
    хотя бы одна строка реально изменилась (`_dirty`).
    """

    def __init__(self, excel_writer: ExcelWriter, sheet_name: str):
        self.excel_writer = excel_writer
        self.sheet_name = sheet_name

    def update_from_result(
        self,
        write_key: WriteKey,
        api_response: Dict[str, Any],
        action: str,
        columns_filter: Optional[List[str]] = None,
    ) -> None:
        self.excel_writer.update_from_api_response(
            self.sheet_name,
            write_key,
            api_response,
            action,
            columns_filter=columns_filter,
        )

    async def update_from_result_async(
        self,
        write_key: WriteKey,
        api_response: Dict[str, Any],
        action: str,
        columns_filter: Optional[List[str]] = None,
    ) -> None:
        await self.excel_writer.update_from_api_response_async(
            self.sheet_name,
            write_key,
            api_response,
            action,
            columns_filter=columns_filter,
        )

    def save(self) -> None:
        self.excel_writer.save()
