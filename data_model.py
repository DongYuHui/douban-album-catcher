import peewee
import datetime

# 数据库对象
db = peewee.SqliteDatabase(None)


class BaseModel(peewee.Model):
    """基础模型

    所有数据库模型均继承该类
    """
    class Meta:
        database = db


class Album(BaseModel):
    """相册信息

    Attributes:
        id: 主键
        title: 标题
        url: 地址
        description: 描述
        count: 图片数量
        page_total: 分页总数
        page_size: 每页数量
        create_time: 创建时间
        modify_time: 修改时间
    """

    id = peewee.AutoField()
    title = peewee.CharField()
    url = peewee.CharField(unique=True)
    description = peewee.CharField()
    count = peewee.IntegerField()
    page_total = peewee.IntegerField()
    page_size = peewee.IntegerField()
    create_time = peewee.DateTimeField(default=datetime.datetime.now)
    modify_time = peewee.DateTimeField(default=datetime.datetime.now)

    def __str__(self):
        return "[Album][id:%s][title:%s][url:%s][description:%s][count:%s][pa ge:%s][page_size:%s][create_time:%s][modiftyTime:%s]" % (self.id, self.title, self.url, self.description, self.count, self.page_total, self.page_size, self.create_time, self.modify_time)


class Image(BaseModel):
    '''相册图片

    Attributes:
        id: 主键
        album_id: 相册 ID
        order_index: 排序码
        name: 图片名称
        url_normal: 获取到的原始地址
        url_large: 替换的大图地址
        url_raw: 替换的原图地址
        file_size: 文件大小：下载后才能知道
        create_time: 创建时间
        modify_time: 更新时间
    '''

    id = peewee.AutoField()
    album_id = peewee.IntegerField()
    order_index = peewee.IntegerField()
    name = peewee.CharField(unique=True)
    url_normal = peewee.CharField()
    url_large = peewee.CharField()
    url_raw = peewee.CharField()
    file_size = peewee.IntegerField(null=True)
    create_time = peewee.DateTimeField(default=datetime.datetime.now)
    modify_time = peewee.DateTimeField(default=datetime.datetime.now)

    def __str__(self):
        return "[Image][id:%s][album_id:%s][order_index:%s][name:%s][url_normal:%s][url_large:%s][url_raw:%s][file_size:%s][create_time:%s][modify_time:%s]" % (self.id, self.album_id, self.order_index, self.name, self.url_normal, self.url_large, self.url_raw, self.file_size, self.create_time, self.modify_time)


def init_database(databasePath):
    """初始化数据库

    Attributes:
        databasePath: 数据库文件路径
    """
    # 初始化数据库
    db.init(databasePath)
    db.connect()
    db.create_tables([Album, Image])
