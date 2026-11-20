#include "core.h"

extern "C" {
typedef void (*S4Progress)(void*, quint64, quint64, const char*);
char* s4_call(const char* method, const char* args, S4Progress cb, void* ud);
void s4_free(char* p);
}

static void progressTrampoline(void* ud, quint64 i, quint64 n, const char* text) {
    const auto* fn = static_cast<const ProgressFn*>(ud);
    (*fn)(int(i), int(n), QString::fromUtf8(text));
}

QJsonValue coreCall(const char* method, const QJsonObject& args, const ProgressFn& progress) {
    const QByteArray a = QJsonDocument(args).toJson(QJsonDocument::Compact);
    char* raw = s4_call(method, a.constData(), progress ? progressTrampoline : nullptr,
                        progress ? const_cast<void*>(static_cast<const void*>(&progress)) : nullptr);
    if (!raw)
        throw CoreError(u"코어가 응답하지 않았습니다."_s);
    const QByteArray bytes(raw);
    s4_free(raw);
    const QJsonObject o = QJsonDocument::fromJson(bytes).object();
    if (o.contains("err"_L1))
        throw CoreError(o.value("err"_L1).toString());
    return o.value("ok"_L1);
}

void fillItem(Item& it, const QJsonObject& o) {
    it.id = o["id"_L1].toInteger();
    it.name = o["name"_L1].toString();
    it.relPath = o["rel_path"_L1].toString();
    it.kind = o["kind"_L1].toString();
    it.category = o["category"_L1].toString();
    it.status = o["status"_L1].toString();
    it.memo = o["memo"_L1].toString();
    it.reason = o["reason"_L1].toString();
    it.userThumb = o["user_thumb"_L1].toString();
    it.displayThumb = o["display_thumb"_L1].toString();
    it.warning = o["warning"_L1].toString();
    it.size = o["size"_L1].toInteger();
    it.mtime = o["mtime"_L1].toDouble();
    it.addedAt = o["added_at"_L1].toDouble();
    it.confidence = o["confidence"_L1].toDouble();
    it.userSet = o["user_set"_L1].toBool();
    it.hq = o["hq"_L1].toBool();
    it.members = o["members"_L1].toInt();
    const QJsonArray tex = o["tex"_L1].toArray();
    it.hasTex = tex.size() >= 2;
    it.texW = it.hasTex ? tex[0].toInt() : 0;
    it.texH = it.hasTex ? tex[1].toInt() : 0;
    it.caspNames.clear();
    for (const auto& n : o["casp_names"_L1].toArray())
        it.caspNames << n.toString();
}

ItemPtr itemFrom(const QJsonValue& v) {
    if (!v.isObject())
        return nullptr;
    auto it = std::make_shared<Item>();
    fillItem(*it, v.toObject());
    return it;
}

QList<ItemPtr> itemsFrom(const QJsonValue& v) {
    QList<ItemPtr> out;
    const QJsonArray a = v.toArray();
    out.reserve(a.size());
    for (const auto& x : a)
        out << itemFrom(x);
    return out;
}

QJsonArray idsOf(const QList<ItemPtr>& items) {
    QJsonArray a;
    for (const auto& it : items)
        a << it->id;
    return a;
}

namespace cats {
QString SCRIPT_MOD, UNSORTED;
QList<std::pair<QString, QString>> GROUPS;
QList<Category> CATEGORIES;

QString label(const QString& key) {
    if (key == SCRIPT_MOD)
        return u"스크립트 모드"_s;
    for (const auto& c : CATEGORIES)
        if (c.key == key)
            return c.label;
    return key;
}

QString groupOf(const QString& key) {
    if (key == SCRIPT_MOD)
        return SCRIPT_MOD;
    for (const auto& c : CATEGORIES)
        if (c.key == key)
            return c.group;
    return UNSORTED;
}

QString groupLabel(const QString& group) {
    for (const auto& g : GROUPS)
        if (g.first == group)
            return g.second;
    return {};
}
}

Core& core() {
    static Core c;
    return c;
}

static void readConfig(const QJsonObject& o) {
    Config& c = cfg();
    c.simsDir = o["sims_dir"_L1].toString();
    c.gameExe = o["game_exe"_L1].toString();
    c.theme = o["theme"_L1].toString();
    c.viewMode = o["view_mode"_L1].toString();
    c.modsDir = o["mods_dir"_L1].toString();
    c.clearThumbcacheOnLaunch = o["clear_thumbcache_on_launch"_L1].toBool();
    c.thumbSize = o["thumb_size"_L1].toInt(128);
}

static QStringList strings(const QJsonValue& v) {
    QStringList out;
    for (const auto& x : v.toArray())
        out << x.toString();
    return out;
}

void coreInit() {
    const QJsonObject o = coreCall("init").toObject();
    Core& c = core();
    readConfig(o["config"_L1].toObject());
    c.appDir = o["app_dir"_L1].toString();
    c.thumbsDir = o["thumbs_dir"_L1].toString();
    c.disabledDir = o["disabled_dir"_L1].toString();
    c.quarantineDir = o["quarantine_dir"_L1].toString();
    c.organizeLabel = o["organize_label"_L1].toString();
    c.maxDepth = o["max_depth"_L1].toInt(5);
    c.clearedLabels = strings(o["cleared_labels"_L1]);
    c.keptLabels = strings(o["kept_labels"_L1]);
    const QJsonObject k = o["cats"_L1].toObject();
    cats::SCRIPT_MOD = k["script"_L1].toString();
    cats::UNSORTED = k["unsorted"_L1].toString();
    cats::GROUPS.clear();
    for (const auto& g : k["groups"_L1].toArray())
        cats::GROUPS.append({g.toArray()[0].toString(), g.toArray()[1].toString()});
    cats::CATEGORIES.clear();
    for (const auto& x : k["categories"_L1].toArray()) {
        const QJsonObject co = x.toObject();
        cats::CATEGORIES.append({co["key"_L1].toString(), co["label"_L1].toString(), co["group"_L1].toString()});
    }
}

void setConfig(const QJsonObject& values, bool save) {
    readConfig(coreCall("config.set", {{"values"_L1, values}, {"save"_L1, save}}).toObject());
}

QString libAbs(const QString& rel) {
    return coreCall("lib.abs", {{"rel"_L1, rel}}).toString();
}

QString rsplitHead(const QString& s) {
    const int i = s.lastIndexOf(u'/');
    return i < 0 ? s : s.left(i);
}

QString rsplitTail(const QString& s) {
    const int i = s.lastIndexOf(u'/');
    return i < 0 ? s : s.mid(i + 1);
}

QString pyDate(double ts) {
    return QDateTime::fromSecsSinceEpoch(qint64(std::floor(ts))).toString(u"yyyy-MM-dd"_s);
}

QString pyPercent(double v) {
    return QString::number(v * 100.0, 'f', 0) + u'%';
}

bool isFile(const QString& p) {
    return !p.isEmpty() && QFileInfo(p).isFile();
}

bool isDir(const QString& p) {
    return !p.isEmpty() && QFileInfo(p).isDir();
}

void Async::start(QObject* ctx, std::function<void()> work, std::function<void()> done) {
    join();
    active = true;
    QPointer<QObject> guard(ctx);
    th = std::thread([guard, work = std::move(work), done = std::move(done)] {
        work();
        if (guard)
            postTo(guard.data(), done);
    });
}

void Async::join() {
    if (th.joinable())
        th.join();
    active = false;
}
