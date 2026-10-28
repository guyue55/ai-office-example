"""模型出口与响应共用的受控异常，不携带供应商正文。"""


class ModelEgressError(Exception):
    """固定错误分类，不携带目标、解析结果或凭证。"""

    def __init__(self, category="policy"):
        self.category = category
        super().__init__(category)
