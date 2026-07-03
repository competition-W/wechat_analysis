from pydantic_settings import BaseSettings
from pydantic import Field
from typing import List
from functools import lru_cache


class Settings(BaseSettings):
    LLM_API_KEY: str = Field(default="sk-39580c767a8641ecb25825fa230a1ad9", description="LLM API密钥")
    LLM_BASE_URL: str = Field(
        default="https://dashscope.aliyuncs.com/compatible-mode/v1",
        description="LLM API基础URL"
    )
    LLM_MODEL_SUMMARY: str = Field(default="qwen-long", description="摘要生成模型")
    LLM_MODEL_SENTIMENT: str = Field(default="qwen-plus", description="情感分析模型")
    
    SENSITIVE_WORDS: str = Field(default="", description="敏感词列表(逗号分隔)")
    
    CUSTOMER_GOOD_WORDS: str = Field(default="", description="客户好评词库(逗号分隔)")
    CUSTOMER_BAD_WORDS: str = Field(default="", description="客户差评词库(逗号分隔)")
    EMPLOYEE_POS_WORDS: str = Field(default="", description="员工积极词库(逗号分隔)")
    EMPLOYEE_BAD_WORDS: str = Field(default="", description="员工恶劣态度词库(逗号分隔)")
    EMPLOYEE_POLITE_WORDS: str = Field(default="", description="员工礼貌用语白名单(逗号分隔)，命中后直接标记为积极态度")
    
    SERVICE_PORT: int = Field(default=8000, description="服务端口")
    SERVICE_HOST: str = Field(default="0.0.0.0", description="服务主机")
    LOG_LEVEL: str = Field(default="INFO", description="日志级别")
    
    LLM_TIMEOUT: int = Field(default=120, description="LLM调用超时时间(秒)")
    LLM_MAX_RETRIES: int = Field(default=2, description="LLM调用最大重试次数")
    LLM_BATCH_SIZE: int = Field(default=50, description="LLM批量分析每批消息数")
    LLM_MAX_CONCURRENT: int = Field(default=10, description="LLM最大并发调用数")
    
    SUMMARY_MAX_MESSAGES: int = Field(default=150, description="摘要最大消息数")
    SUMMARY_MAX_LENGTH: int = Field(default=1000, description="摘要最大长度")
    HIGH_FREQ_TOP_N: int = Field(default=20, description="高频词返回数量")

    JAVA_DATA_SOURCE_URL: str = Field(
        default="http://192.168.0.129:8081/qxChat/",
        description="Java数据源接口地址"
    )
    JAVA_DATA_SOURCE_TIMEOUT: int = Field(default=120, description="Java接口超时时间(秒)")
    

    # ========== LIMS API 配置 (新增) ==========
    LIMS_API_URL: str = Field(
        default="http://110.1.1.96:8080/unionLims/",
        description="LIMS API 基础地址"
    )
    LIMS_BASE_DATA_PATH: str = Field(
        default="/base_data/",
        description="LIMS base_data 接口路径"
    )
    LIMS_API_TIMEOUT: int = Field(default=30, description="LIMS API 超时时间(秒)")

    PROJECT_CODE_PATTERN: str = Field(
        default=r"LC-P\d+",
        description="从群名称提取项目编号的正则表达式"
    )

    # ========== 报告生成配置 (新增) ==========
    REPORT_OUTPUT_DIR: str = Field(default="./reports", description="报告输出目录")
    REPORT_TITLE: str = Field(default="群聊数据统计分析报告", description="报告标题")

    ARCHIVE_DIR: str = Field(default="../../archive", description="数据归档目录")
    @property
    def sensitive_words_list(self) -> List[str]:
        return [w.strip() for w in self.SENSITIVE_WORDS.split(",") if w.strip()]
    
    @property
    def customer_good_words_list(self) -> List[str]:
        return [w.strip() for w in self.CUSTOMER_GOOD_WORDS.split(",") if w.strip()]
    
    @property
    def customer_bad_words_list(self) -> List[str]:
        return [w.strip() for w in self.CUSTOMER_BAD_WORDS.split(",") if w.strip()]
    
    @property
    def employee_pos_words_list(self) -> List[str]:
        return [w.strip() for w in self.EMPLOYEE_POS_WORDS.split(",") if w.strip()]
    
    @property
    def employee_bad_words_list(self) -> List[str]:
        return [w.strip() for w in self.EMPLOYEE_BAD_WORDS.split(",") if w.strip()]
    
    @property
    def employee_polite_words_list(self) -> List[str]:
        return [w.strip() for w in self.EMPLOYEE_POLITE_WORDS.split(",") if w.strip()]
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
