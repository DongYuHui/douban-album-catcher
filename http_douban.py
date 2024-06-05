import os
import re
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from data_model import Album, Image, init_database

headers = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36"
}


def catch_album_info(url):
    """获取相册信息

    Args:
        url: 相册地址

    Returns:
        相册信息
    """
    url = urljoin(url, urlparse(url).path)
    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        print("catch album info error " + str(response.status_code))
        return None
    soup = BeautifulSoup(response.text, "lxml")
    # 一、相册信息
    album = Album()
    # 1.1 地址
    album.url = url
    # 1.2 标题
    album.title = soup.title.string
    # 1.3 描述
    soupDesc = soup.find("p", class_="description")
    album.description = soupDesc.string if soupDesc.string != None else ""
    # 1.4 数量
    soupCountSpan = soup.find("div", class_="photitle").span
    soupCountStr = re.findall(r"\d+", soupCountSpan.string)
    album.count = int(soupCountStr[0]) if len(soupCountStr) > 0 else None
    # 二、分页信息
    # 2.1 每页数量
    album.page_size = 18  # 目前豆瓣固定的 18 个每页
    # 2.2 分页总数
    soupPage = soup.find("div", class_="paginator")
    if soupPage != None:
        soupPageInfo = soupPage.find("span", class_="thispage")
        album.page_total = int(soupPageInfo["data-total-page"])
    else:
        album.page_total = 1  # 没有分页说明只有一页
    # 三、输出信息
    print("相册信息：%s" % album)
    return album


def catch_album_page(album, start):
    """获取图片分页

    Args:
        album: 相册信息
        start: 分页开始索引

    Returns:
        图片列表
    """
    url = album.url + "?m_start=" + str(start)
    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        print(
            "catch album page error, url: [%s], status_code: [%d] "
            & (url, response.status_code)
        )
        return None
    soup = BeautifulSoup(response.text, "lxml")
    # 一、获取图片列表
    soupImageBox = soup.find("div", class_="photolst")
    soupImageList = soup.find_all("a", class_="photolst_photo")
    # 二、获取图片
    images = []
    for index in range(len(soupImageList)):
        soupImageItem = soupImageList[index]
        imageUrl = soupImageItem.img["src"]
        # 组装图片信息
        image = Image()
        image.album_id = album.id
        # 2.1 排序
        image.order_index = start + index + 1
        # 2.2 编码
        image_name_start = imageUrl.rfind("/")
        image.name = imageUrl[image_name_start + 1 :]
        # 2.3 描述
        imagePageUrl = soupImageItem["href"]
        image.note = catch_image_note(imagePageUrl)
        # 2.4 地址
        image.url_normal = imageUrl
        image.url_large = imageUrl.replace("/m/", "/l/")
        image.url_raw = imageUrl.replace("/m/", "/r/")
        images.append(image)
    # 三、输出信息
    image_names = map(lambda item: item.name, images)
    print("图片分页信息：%s" % str(list(image_names)))
    return images


def catch_image_note(imageUrl):
    """获取图片描述

    Args:
        imageUrl: 图片地址

    Returns:
        图片列表
    """
    url = imageUrl
    print("图片信息地址：%s" % str(url))
    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        print(
            "catch image info error, url: [%s], status_code: [%d] "
            & (url, response.status_code)
        )
        return None
    soup = BeautifulSoup(response.text, "lxml")
    # 获取图片描述
    descBox = soup.find("div", class_="photo_descri")
    imageNote = None
    if descBox != None:
        imageNote = descBox.div.string
    print("图片描述信息：%s" % str(imageNote))
    return imageNote


def download_image(image, directory):
    # 判断目录是否存在
    dir_exist = os.path.exists(directory)
    if not dir_exist:
        os.makedirs(directory)
    # 判断本地文件是否存在
    file_path_prefix = directory if directory.endswith("/") else directory + "/"
    file_index = "{:0>4d}".format(image.order_index)
    file_path = file_path_prefix + file_index + "-" + image.name
    file_exist = os.path.exists(file_path)
    if image.file_size != None and file_exist:
        # 如果本地文件的大小符合下载文件的大小，则无须重复下载
        file_size = os.path.getsize(file_path)
        if image.file_size == file_size:
            print("图片已存在，跳过下载：" + file_index + " - " + image.name)
            return
    # 下载文件
    response = requests.get(image.url_raw, headers=headers)
    if response.status_code != 200:
        print(
            "图片下载失败："
            + file_index
            + " - "
            + image.name
            + " - "
            + str(response.status_code)
        )
    with open(file_path, "wb") as f:
        f.write(response.content)
    # 保存文件大小
    response_length = response.headers.get("Content-Length")
    image_update = Image.update({Image.file_size: int(response_length)}).where(
        Image.id == image.id
    )
    effect_row = image_update.execute()
    if effect_row != 1:
        print("图片下载成功，但更新记录失败：" + file_index + " - " + image.name)
    else:
        print("图片下载成功：" + file_index + " - " + image.name)


if __name__ == "__main__":

    # 初始化数据库
    init_database("album.db")

    # 获取相册
    # album_url = "https://www.douban.com/photos/album/1874833760"
    album_url = "https://www.douban.com/photos/album/1887978860"
    album_info = catch_album_info(album_url)
    if album_info == None:
        print("获取相册信息失败")
        sys.exit()
    album_query = Album.select().where(Album.url == album_info.url).first()
    if album_query != None:
        album_info.id = album_query.id
    album_info.save()

    # 获取图片
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
