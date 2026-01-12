from bs4 import BeautifulSoup
import json

class HtmlToGutenberg:
    def __init__(self):
        self.tag_mapping = {
            'p': self._convert_paragraph,
            'h1': self._convert_heading,
            'h2': self._convert_heading,
            'h3': self._convert_heading,
            'h4': self._convert_heading,
            'h5': self._convert_heading,
            'h6': self._convert_heading,
            'img': self._convert_image,
            'ul': self._convert_list,
            'ol': self._convert_list,
            'table': self._convert_table,
            'blockquote': self._convert_blockquote,
            'strong': self._convert_strong,
            'b': self._convert_bold,
            'em': self._convert_emphasis,
            'i': self._convert_italic,
        }

    def convert(self, html):
        soup = BeautifulSoup(html, 'html.parser')
        gutenberg_blocks = []

        for element in soup.find_all(recursive=False):
            if element.name in self.tag_mapping:
                block = self.tag_mapping[element.name](element)
                if block:
                    gutenberg_blocks.append(block)

        return '\n'.join(gutenberg_blocks)

    def _convert_paragraph(self, element):
        content = str(element).replace('\n', '').strip()
        return f"<!-- wp:paragraph -->\n{content}\n<!-- /wp:paragraph -->"

    def _convert_heading(self, element):
        level = int(element.name[1])
        content = element.decode_contents().strip()
        return f"<!-- wp:heading {{\"level\":{level}}} -->\n<{element.name}>{content}</{element.name}>\n<!-- /wp:heading -->"

    def _convert_image(self, element):
        src = element.get('src', '')
        alt = element.get('alt', '')
        if not src:
            return None
        attrs = {"src": src}
        if alt:
            attrs["alt"] = alt
        attrs_json = json.dumps(attrs)
        return f"<!-- wp:image {attrs_json} -->\n<figure class=\"wp-block-image\"><img src=\"{src}\" alt=\"{alt}\"/></figure>\n<!-- /wp:image -->"

    def _convert_list(self, element):
        ordered = element.name == 'ol'
        items = [f"<li>{li.decode_contents().strip()}</li>" for li in element.find_all('li', recursive=False)]
        list_content = '\n'.join(items)
        tag = 'ol' if ordered else 'ul'
        attrs = {"ordered": True} if ordered else {}
        attrs_json = json.dumps(attrs)
        return f"<!-- wp:list {attrs_json} -->\n<{tag}>\n{list_content}\n</{tag}>\n<!-- /wp:list -->"

    def _convert_table(self, element):
        thead = element.find('thead')
        tbody = element.find('tbody')
        if thead or tbody:
            table_content = ''
            if thead:
                table_content += str(thead).replace('\n', '').strip()
            if tbody:
                table_content += str(tbody).replace('\n', '').strip()
            if not thead and not tbody:
                table_content = str(element).replace('\n', '').strip()
        else:
            table_content = str(element).replace('\n', '').strip()
        return f"<!-- wp:table -->\n<figure class=\"wp-block-table\"><table class=\"has-fixed-layout\">{table_content}</table></figure>\n<!-- /wp:table -->"

    def _convert_blockquote(self, element):
        content = element.decode_contents().strip()
        return f"<!-- wp:quote -->\n<blockquote class=\"wp-block-quote\">{content}</blockquote>\n<!-- /wp:quote -->"

    def _convert_strong(self, element):
        content = element.decode_contents().strip()
        return f"<!-- wp:paragraph -->\n<p><strong>{content}</strong></p>\n<!-- /wp:paragraph -->"

    def _convert_bold(self, element):
        content = element.decode_contents().strip()
        return f"<!-- wp:paragraph -->\n<p><b>{content}</b></p>\n<!-- /wp:paragraph -->"

    def _convert_emphasis(self, element):
        content = element.decode_contents().strip()
        return f"<!-- wp:paragraph -->\n<p><em>{content}</em></p>\n<!-- /wp:paragraph -->"

    def _convert_italic(self, element):
        content = element.decode_contents().strip()
        return f"<!-- wp:paragraph -->\n<p><i>{content}</i></p>\n<!-- /wp:paragraph -->"