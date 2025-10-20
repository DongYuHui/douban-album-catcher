import sys

from data_model import Album, Image, init_database
from http_douban import catch_album_info, catch_album_page, download_image

if __name__ == "__main__":

    # 初始化数据库
    init_database("album.db")

    # 获取相册
    # album_url = "https://www.douban.com/photos/album/1874833760"
    album_url = "https://www.douban.com/photos/album/106323932"
    album_info = catch_album_info(album_url)
    if album_info == None:
        print("获取相册信息失败")
        sys.exit()
    album_query = Album.select().where(Album.url == album_info.url).first()
    if album_query != None:
        album_info.id = album_query.id
    album_info.save()

    # 获取图片，按页遍历
    for index in range(album_info.page_total):

        # 获取分页
        album_page = catch_album_page(album_info, index * 18)
        if album_page == None:
            print("获取相册分页失败")
            sys.exit()
        for image_item in album_page:
            image_query = Image.select().where(Image.name == image_item.name).first()
            if image_query == None:
                image_item.save()
            else:
                image_item.id = image_query.id
                image_item.file_size = image_query.file_size

        # 下载图片
        for image_item in album_page:
            try:
                download_image(image_item, "image/")
            except BaseException:
                print("下载图片异常：%s" % str(image_item))
