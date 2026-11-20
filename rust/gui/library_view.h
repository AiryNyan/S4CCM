// gui/library_view.py
#pragma once

#include "common.h"

extern const QString INSTALLED;
constexpr int ItemRole = Qt::UserRole + 1;

class ItemModel : public QAbstractTableModel {
public:
    QList<ItemPtr> items;
    std::function<void(const ItemPtr&, bool)> checkToggled;

    void setItems(const QList<ItemPtr>& list);
    int rowCount(const QModelIndex& parent = {}) const override;
    int columnCount(const QModelIndex& parent = {}) const override;
    QVariant headerData(int section, Qt::Orientation orientation, int role = Qt::DisplayRole) const override;
    QVariant data(const QModelIndex& index, int role = Qt::DisplayRole) const override;
    Qt::ItemFlags flags(const QModelIndex& index) const override;
    bool setData(const QModelIndex& index, const QVariant& value, int role = Qt::EditRole) override;
};

class ItemFilter : public QSortFilterProxyModel {
public:
    ItemFilter();
    // 빈 문자열은 Python 의 None.
    void setScope(const QString& group, const QString& category, const QString& status);
    void setText(const QString& text);

protected:
    bool filterAcceptsRow(int row, const QModelIndex& parent) const override;

private:
    QString group_, category_, status_ = INSTALLED, text_;
};

class GridDelegate : public QStyledItemDelegate {
public:
    static constexpr int PAD = 8;
    int icon;

    GridDelegate(QObject* parent, int size) : QStyledItemDelegate(parent), icon(size) {}
    QSize cell() const { return QSize(icon + 28, icon + 48); }
    QSize sizeHint(const QStyleOptionViewItem&, const QModelIndex&) const override { return cell(); }
    void paint(QPainter* p, const QStyleOptionViewItem& option, const QModelIndex& index) const override;
    bool editorEvent(QEvent* event, QAbstractItemModel* model, const QStyleOptionViewItem& option, const QModelIndex& index) override;

private:
    QRect imageRect(const Item& item, const QRect& rect) const;
    QRect badgeRect(const Item& item, const QRect& rect) const;
};

class ElideLabel : public QLabel {
public:
    explicit ElideLabel(Qt::TextElideMode mode = Qt::ElideMiddle);
    void setFull(const QString& text);
    QSize minimumSizeHint() const override;

protected:
    void resizeEvent(QResizeEvent* e) override;

private:
    void elide();
    QString full_;
    Qt::TextElideMode mode_;
};

class ThumbLabel : public QLabel {
public:
    std::function<void(const QString&)> dropped;
    std::function<void(int)> step;   // 좌우 화살표·휠·←/→ 로 이미지 넘기기 (-1 / +1)
    ThumbLabel();
    void setNavVisible(bool on);

protected:
    void dragEnterEvent(QDragEnterEvent* e) override;
    void dropEvent(QDropEvent* e) override;
    void wheelEvent(QWheelEvent* e) override;
    void keyPressEvent(QKeyEvent* e) override;

private:
    QToolButton *prev, *next;
};

class DetailPanel : public QFrame {
public:
    std::function<void()> changed;
    std::function<void(const QString&)> request;
    QList<ItemPtr> items;

    DetailPanel();
    void showItems(const QList<ItemPtr>& list);

private:
    void showFacts(const QHash<QString, QString>& values, const QHash<QString, QString>& tooltip = {});
    void saveNote();
    void categoryChanged();
    void setThumbFile(const QString& path);
    void pickThumb();
    void pasteThumb();
    void emitRequest(const char* action);
    void showPose();   // 포즈 팩 아이콘 중 poseIndex 번째를 보여 준다

    ThumbLabel* thumb;
    QLabel* poseCount;          // "3 / 27"
    QStringList poseImages;     // 2장 이상일 때만 채운다
    int poseIndex = 0;
    int poseGen = 0;            // 선택이 바뀌면 늘려서 늦게 온 결과를 버린다
    Async poseTask;
    QLabel *title, *warn, *reason, *info;
    CategoryCombo* cat;
    QPlainTextEdit* memo;
    QWidget* facts;
    QList<std::pair<QString, std::pair<QLabel*, ElideLabel*>>> factRows;
    QTimer* saveTimer;
    QPushButton *bOpen, *bOn, *bOff, *bDel;
};

class LibraryPage : public Page {
public:
    std::function<void()> countsChanged;
    std::function<void(const QList<ItemPtr>&, const QString&)> statusRequested;
    ItemModel* model;
    DetailPanel* detail;

    LibraryPage();
    bool hasRefresh() const override { return false; }
    void zoom(int steps);
    void setViewMode(const QString& mode);
    QList<ItemPtr> selected() const;
    void setScope(const QString& title, const QString& group, const QString& category, const QString& status);
    void setSearch(const QString& text);
    void reload();

protected:
    bool eventFilter(QObject* obj, QEvent* e) override;

private:
    int savedThumbSize() const;
    void applyThumbSize();
    void setThumbSize(int size);   // 슬라이더와 Ctrl+휠이 함께 쓰는 크기 변경
    QAbstractItemView* currentView() const;
    void selectionChanged();
    void contextMenu(const QPoint& pos);
    void setCategory(const QList<ItemPtr>& list, const QString& key);

    ItemFilter* proxy;
    QListView* grid;
    GridDelegate* gridDelegate;
    QTableView* table;
    QStackedWidget* views;
    QLabel* header;
    QWidget* zoomBar;    // 그리드 아래 썸네일 크기 조절 (Python 판에는 없음)
    QSlider* zoomSlider;
    QString title_;
};
