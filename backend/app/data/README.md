# 天气城市目录

weather-cities.json 是 GeoNames 官方 cities15000.zip 的筛选与字段转换快照，包含来源数据中区域代码 CN/HK/MO/TW 的城市、城镇参考点；不是完整行政城市名录，也不是实时地理定位。当前为 2026-10-08 获取的 2,336 个条目。

原数据与行政区名称来自：
- https://download.geonames.org/export/dump/cities15000.zip
- https://download.geonames.org/export/dump/admin1CodesASCII.txt
- 格式说明：https://download.geonames.org/export/dump/readme.txt

GeoNames (www.geonames.org), CC BY 4.0： https://creativecommons.org/licenses/by/4.0/ 。已筛选并转换为JSON，保留GeoNames编号、逐条来源链接、原始ASCII名称与别名、区域代码、城市参考坐标和时区；中文显示名优先使用数据中的中文别名，不冒充官方规范名称。文件 metadata 记录抓取日期、原文件SHA-256与修改说明。

仅用于天气地点选择，坐标不是用户住址、路线或准确位置。运行时不联网更新；无法查询到的地点保留未找到状态，可由用户选择在线搜索。不得由模型编造坐标。

显式维护命令（会下载公开资料并更新快照）：

```bash
backend/.venv/bin/python scripts/build_city_catalog.py --download-public-data --snapshot-date YYYY-MM-DD
```

下载无需个人资料，限定官方HTTPS域名、压缩文件大小与单个文本成员大小；不执行下载内容、不写解压路径。更新后须重新核对条目/时区和页面行为，不假设供应商数据长期不变。
