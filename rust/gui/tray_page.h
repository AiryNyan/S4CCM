// gui/tray_page.py
#pragma once

#include "common.h"

struct TraySkill {
    QString name;
    int level = 0, maxLevel = 0;
};

struct TrayRelation {
    QString name;
    bool hasFriendship = false, hasRomance = false;
    double friendship = 0, romance = 0;
};

struct TraySim {
    QString fullName, summary, aspiration;
    QStringList traits, likes, dislikes;
    QList<TraySkill> skills;
    bool hasRelations = false;
    QString relSource;
    QList<TrayRelation> relations;
};

struct TrayEntry {
    QString key, kindLabel, name, creator, description, savedAt, thumb, exportName;
    int kind = 0, files = 0;
    bool hasMainImages = false;
    QList<TraySim> sims;
};
using TrayEntryPtr = std::shared_ptr<TrayEntry>;

class SkillBars : public QWidget {
public:
    static constexpr int NAME_W = 78, CELL = 9, GAP = 2, ROW_H = 22, COLS = 2;
    explicit SkillBars(const QList<TraySkill>& skills);
    QSize sizeHint() const override;
    QSize minimumSizeHint() const override { return sizeHint(); }

protected:
    void paintEvent(QPaintEvent*) override;

private:
    int rows() const { return (int(skills_.size()) + COLS - 1) / COLS; }
    int colWidth() const { return NAME_W + 8 + 10 * (CELL + GAP) + 14; }
    QList<TraySkill> skills_;
};

class TrayPage : public Page {
public:
    TrayPage();
    void refreshPage() override { refresh(false); }
    void refresh(bool force);
    void forgetLoaded() { loaded_ = false; }   // self.tray._loaded_dir = None

private:
    struct Pending {
        bool hasName = false, hasThumb = false, hasDesc = false, clean = false;
        QString name, thumb, desc;
        bool empty() const { return !hasName && !hasThumb && !hasDesc && !clean; }
    };
    QString trayDir() const;
    TrayEntryPtr currentEntry() const;
    void fill();
    void clearSims();
    void show(const TrayEntryPtr& e);
    QWidget* simCard(const TraySim& sim, const QString& portrait, bool cleaned);
    QString thumbOf(const TrayEntry& e) const;
    void staged(const TrayEntryPtr& e);
    void replaceThumb();
    void lookOnly();
    void discard();
    void stageText(const TrayEntryPtr& e, bool isName, const QString& text, const QString& original);
    void rename();
    void editDesc();
    void deleteEntry();
    TrayEntryPtr saveCopy();
    void exportEntry();

    QList<TrayEntryPtr> entries;
    bool loaded_ = false;
    QString loadedDir_;
    QHash<QString, Pending> pending;
    QString selectKey_;
    TrayEntryPtr current;
    int usedCcCount_ = 0;

    QComboBox* kind;
    QLineEdit* search;
    QLabel *count, *image, *name, *sub, *desc, *pendingLabel, *simsTitle, *ccTitle, *ccHint;
    QListWidget* list;
    QWidget *detail, *actions;
    QPushButton *bThumb, *bClean, *bExport, *bRename, *bDesc, *bDelete, *bSave, *bDiscard;
    QFrame* pendingBar;
    QVBoxLayout* simsBox;
    QTreeWidget* cc;
    QScrollArea* scroll;
};
