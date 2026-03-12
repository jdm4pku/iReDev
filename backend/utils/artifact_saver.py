import os
import re
from typing import Optional


class ArtifactSaver:
	"""将 title + content 写出到指定目录的文件写入器。"""

	def __init__(self, default_extension: str = ".md"):
		self.default_extension = default_extension if default_extension.startswith(".") else f".{default_extension}"

	def _sanitize_filename(self, title: str) -> str:
		clean_title = title.strip() if title else "untitled"
		clean_title = re.sub(r"[\\/:*?\"<>|]", "_", clean_title)
		clean_title = re.sub(r"\s+", "_", clean_title)
		return clean_title or "untitled"

	def write(
		self,
		title: str,
		content: str,
		directory: str,
		extension: Optional[str] = None,
		encoding: str = "utf-8",
	) -> str:
		"""
		根据标题在目录中创建文件并写入内容。

		Args:
			title: 文件标题（将作为文件名主体）
			content: 文件内容
			directory: 输出目录
			extension: 文件后缀（可选，默认使用初始化时配置）
			encoding: 写文件编码

		Returns:
			写出的文件绝对路径
		"""
		if not directory:
			raise ValueError("directory cannot be empty")

		file_extension = extension or self.default_extension
		if not file_extension.startswith("."):
			file_extension = f".{file_extension}"

		filename = f"{self._sanitize_filename(title)}{file_extension}"
		output_dir = os.path.abspath(directory)
		os.makedirs(output_dir, exist_ok=True)

		file_path = os.path.join(output_dir, filename)
		with open(file_path, "w", encoding=encoding) as f:
			f.write(content or "")

		return file_path
