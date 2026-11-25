// gui/common.py, gui/loading.py 에 해당하는 공용 부분.
#pragma once

#include "core.h"

extern const QHash<QString, QString> STATUS_LABEL_MAP;
QString statusLabel(const QString& status);   // STATUS_LABEL.get(status, status)

QString tok(const char* name);                // 현재 테마의 색
QStringList uiFamilies();
bool usesNanum();
QString humanSize(qint64 n);
QString categoryPathLabel(const QString& key);
QPixmap placeholder(const QString& group, int size = 160);
QIcon itemIcon(const Item& item);
QPixmap itemPixmap(const Item& item, int size);
void forgetIcon(const QString& path);
void reveal(const QString& path);
bool confirm(QWidget* parent, const QString& title, const QString& text);
QString resolveTheme(const QString& theme);
void applyTheme(const QString& theme);
void repolish(QWidget* w);                    // w.style().polish(w)

class CategoryCombo : public QComboBox {
public:
    explicit CategoryCombo(QWidget* parent = nullptr, bool includeScript = false);
    void setKey(const QString& key);
    QString key() const { return currentData().toString(); }
};

// ---- loading.py

using LoadProgress = std::function<void(int, int, const QString&, const QString&)>;

bool isBusy();
// 창 위에 진행 표시를 띄우고 fn 을 다른 스레드에서 실행한다. fn 이 던진 CoreError 는 그대로 다시 던진다.
// 로딩 창 두 번째 줄에 돌아가며 보여 줄 멘트 묶음. None 이면 단계 이름을 보여 준다.
enum class Quips { None, Common, Tray, Organize, Import, Bisect, Update };
QStringList quipsFor(Quips set);   // 공통 + 묶음 멘트 (quips.cpp)
// showCount 가 false 면 "3 / 80" 같은 개수 칸을 숨긴다 (다운로드처럼 개수가 의미 없을 때).
QJsonValue runWithLoading(QWidget* parent, const QString& title, const std::function<QJsonValue(const LoadProgress&)>& fn,
                          Quips quips = Quips::None, bool showCount = true);

// 진행 단계 이름을 붙여 코어 호출에 넘길 콜백을 만든다.
inline ProgressFn phased(const LoadProgress& progress, const QString& phase) {
    return [progress, phase](int i, int n, const QString& text) { progress(i, n, text, phase); };
}

// 코어가 "단계\x01이름" 형식으로 단계 이름까지 알려 주는 호출(가져오기)에 쓰는 콜백.
inline ProgressFn stepped(const LoadProgress& progress) {
    return [progress](int i, int n, const QString& text) {
        const qsizetype cut = text.indexOf(QChar(1));
        if (cut < 0)
            progress(i, n, text, QString());
        else
            progress(i, n, text.mid(cut + 1), text.left(cut));
    };
}

// 화면에서 같이 쓰는 작은 도우미.
QLabel* mutedLabel(const QString& text = {});
QWidget* pageTitle(const QString& text, const QString& sub = {}, bool alwaysSub = false);

// 페이지 공통: 화면에 들어올 때 불리는 refresh.
class Page : public QWidget {
public:
    virtual bool hasRefresh() const { return true; }
    virtual void refreshPage() {}
};

struct WaitCursor {
    WaitCursor() { QApplication::setOverrideCursor(Qt::WaitCursor); }
    ~WaitCursor() { QApplication::restoreOverrideCursor(); }
};
