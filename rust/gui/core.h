// Rust 코어(s4ccm_rs.dll)를 부르는 얇은 층과, 화면 전체가 같이 쓰는 자료형.
#pragma once

#include <QtCore>
#include <QtGui>
#include <QtWidgets>

#include <functional>
#include <memory>
#include <stdexcept>
#include <thread>

using namespace Qt::StringLiterals;

// 코어가 돌려준 오류 (Python 판의 예외에 해당).
struct CoreError : std::runtime_error {
    QString msg;
    explicit CoreError(const QString& m) : std::runtime_error(m.toStdString()), msg(m) {}
};

using ProgressFn = std::function<void(int, int, const QString&)>;

// 코어 호출. 실패하면 CoreError 를 던진다. progress 는 호출한 스레드에서 불린다.
QJsonValue coreCall(const char* method, const QJsonObject& args = {}, const ProgressFn& progress = {});

struct Item {
    qint64 id = 0;
    QString name, relPath, kind, category, status, memo, reason, userThumb, displayThumb, warning;
    qint64 size = 0;
    double mtime = 0, addedAt = 0, confidence = 0;
    bool userSet = false, hq = false, hasTex = false;
    int texW = 0, texH = 0, members = 0;
    QStringList caspNames;
};
using ItemPtr = std::shared_ptr<Item>;
Q_DECLARE_METATYPE(ItemPtr)

void fillItem(Item& it, const QJsonObject& o);
ItemPtr itemFrom(const QJsonValue& v);
QList<ItemPtr> itemsFrom(const QJsonValue& v);
QJsonArray idsOf(const QList<ItemPtr>& items);

namespace cats {
struct Category {
    QString key, label, group;
};
extern QString SCRIPT_MOD, UNSORTED;
extern QList<std::pair<QString, QString>> GROUPS;
extern QList<Category> CATEGORIES;
QString label(const QString& key);
QString groupOf(const QString& key);
QString groupLabel(const QString& group);
}

struct Config {
    QString simsDir, gameExe, theme, viewMode, modsDir;
    bool clearThumbcacheOnLaunch = false;
    int thumbSize = 128;
};

struct Core {
    Config config;
    QString appDir, thumbsDir, disabledDir, quarantineDir, organizeLabel;
    QStringList clearedLabels, keptLabels;
    int maxDepth = 5;
};
Core& core();
inline Config& cfg() { return core().config; }

void coreInit();                                                  // Config.load() + Library(config)
void setConfig(const QJsonObject& values, bool save = false);     // 값을 바꾸고, save 면 파일에 쓴다
inline void saveConfig() { setConfig({}, true); }
QString libAbs(const QString& rel);                               // Library.abs

// Python 의 str.rsplit("/", 1) 앞/뒤 조각.
QString rsplitHead(const QString& s);
QString rsplitTail(const QString& s);
QString pyDate(double ts);                                        // strftime("%Y-%m-%d")
QString pyPercent(double v);                                      // f"{v:.0%}"
bool isFile(const QString& p);
bool isDir(const QString& p);

// 백그라운드 작업 하나 (Python 판의 QThread + Worker 쌍).
struct Async {
    std::thread th;
    bool active = false;
    // work 는 새 스레드에서, done 은 ctx 의 스레드(화면)에서 실행된다.
    void start(QObject* ctx, std::function<void()> work, std::function<void()> done);
    void join();
    ~Async() { join(); }
};

// 다른 스레드에서 화면 스레드로 일을 넘긴다. ctx 가 사라졌으면 버려진다.
template <typename F>
void postTo(QObject* ctx, F&& fn) {
    QMetaObject::invokeMethod(ctx, std::forward<F>(fn), Qt::QueuedConnection);
}
