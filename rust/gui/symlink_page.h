// 도구 › 심볼릭 링크: Mods 를 다른 곳으로 옮기고 원래 자리에 링크를 남긴다. (Python 판에는 없는 기능)
#pragma once

#include "common.h"

class SymlinkPage : public Page {
public:
    std::function<void()> changed;                          // 옮기거나 되돌린 뒤 (목록 다시 읽기)
    std::function<void(const QString&)> requestElevation;   // 관리자 권한으로 다시 켜서 옮기기

    SymlinkPage();
    void refreshPage() override;
    // 확인 없이 바로 옮긴다 (확인 창을 지난 뒤, 또는 관리자 권한으로 다시 켜진 뒤). 성공하면 true.
    bool runRelocate(const QString& dest, bool elevated = false);

private:
    QString destMods(const QString& dest) const;
    void pickDest();
    void startMove();
    void restore();

    QLabel* statusText;
    QLineEdit* destEdit;
    QLabel* destInfo;
    QPushButton *bPick, *bMove, *bRestore;
    bool linked_ = false;
};
