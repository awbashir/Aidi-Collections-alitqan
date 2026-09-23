import { useState, useEffect } from "react";
import classNames from "classnames";
import Menu from "@/components/ui/Menu";
import AuthorityCheck from "@/components/shared/AuthorityCheck";
import VerticalSingleMenuItem from "./VerticalSingleMenuItem";
import VerticalCollapsedMenuItem from "./VerticalCollapsedMenuItem";
import { themeConfig } from "@/configs/theme.config";
import {
  NAV_ITEM_TYPE_TITLE,
  NAV_ITEM_TYPE_COLLAPSE,
  NAV_ITEM_TYPE_ITEM,
} from "@/constants/navigation.constant";
import useMenuActive from "@/utils/hooks/useMenuActive";
import { useTranslation } from "react-i18next";
import { Direction, NavMode } from "@/@types/theme";
import type { NavigationGroup, NavigationTree } from "@/@types/navigation";
import { useAppSelector } from "@/store";
import { navigationGroups as defaultNavigationGroups } from "@/configs/navigation.config";
import { withDerivedPermissions } from "@/utils/v2/derivedPermissions";

export interface VerticalMenuContentProps {
  navMode: NavMode;
  collapsed?: boolean;
  routeKey: string;
  navigationTree?: NavigationTree[];
  userAuthority: string[];
  onMenuItemClick?: () => void;
  direction?: Direction;
  /** Titled sections for items that carry a `group`. Defaults to the app's
   *  sidebar groups; trees without grouped items render flat, as before. */
  navigationGroups?: NavigationGroup[];
}

const { MenuGroup } = Menu;

const VerticalMenuContent = (props: VerticalMenuContentProps) => {
  const {
    navMode = themeConfig.navMode,
    collapsed,
    routeKey,
    navigationTree = [],
    userAuthority = [],
    onMenuItemClick,
    direction = themeConfig.direction,
    navigationGroups = defaultNavigationGroups,
  } = props;

  const { t } = useTranslation();

  const [defaulExpandKey, setDefaulExpandKey] = useState<string[]>([]);

  const { activedRoute } = useMenuActive(navigationTree, routeKey);

  useEffect(() => {
    if (defaulExpandKey.length === 0 && activedRoute?.parentKey) {
      setDefaulExpandKey([activedRoute?.parentKey]);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activedRoute?.parentKey]);

  const handleLinkClick = () => {
    onMenuItemClick?.();
  };

  const getNavItem = (nav: NavigationTree) => {
    if (nav.subMenu.length === 0 && nav.type === NAV_ITEM_TYPE_ITEM) {
      return (
        <VerticalSingleMenuItem
          key={nav.key}
          nav={nav}
          sideCollapsed={collapsed}
          userAuthority={userAuthority}
          direction={direction}
          onLinkClick={handleLinkClick}
        />
      );
    }

    if (nav.subMenu.length > 0 && nav.type === NAV_ITEM_TYPE_COLLAPSE) {
      return (
        <VerticalCollapsedMenuItem
          key={nav.key}
          nav={nav}
          sideCollapsed={collapsed}
          userAuthority={userAuthority}
          direction={direction}
          onLinkClick={onMenuItemClick}
        />
      );
    }

    if (nav.type === NAV_ITEM_TYPE_TITLE) {
      if (nav.subMenu.length > 0) {
        return (
          <AuthorityCheck
            key={nav.key}
            userAuthority={userAuthority}
            authority={nav.authority}
          >
            <MenuGroup
              label={t(nav.translateKey) || nav.title}
              className={nav.group ? "menu-group-nested" : undefined}
            >
              {nav.subMenu.map((subNav) =>
                subNav.subMenu.length > 0 ? (
                  <VerticalCollapsedMenuItem
                    key={subNav.key}
                    nav={subNav}
                    sideCollapsed={collapsed}
                    userAuthority={userAuthority}
                    direction={direction}
                    onLinkClick={onMenuItemClick}
                  />
                ) : (
                  <VerticalSingleMenuItem
                    key={subNav.key}
                    nav={subNav}
                    sideCollapsed={collapsed}
                    userAuthority={userAuthority}
                    direction={direction}
                    onLinkClick={onMenuItemClick}
                  />
                )
              )}
            </MenuGroup>
          </AuthorityCheck>
        );
      } else {
        <MenuGroup label={nav.title} />;
      }
    }
  };

  const { permissions } = useAppSelector((state) => state.auth.user);

  /* A stale persisted store (or a user record that predates permissions) has
   * no `permissions` object at all — reading it unguarded took the whole shell
   * down before the nav could render. */
  /* Derived keys (`bulk_jobs`: any bulk kind key, mirroring GET /v2/jobs)
   * are added; real keys are unchanged. */
  const perm = withDerivedPermissions(permissions) as Record<string, boolean>;

  /* Permission filtering is unchanged: a top-level item shows only when its
   * key is granted. Grouping happens after it, so a group whose items are all
   * filtered out never renders its header. */
  /* Sub-items: hide one whose key is a permission the user explicitly lacks
   * (`false`). Keys that are not permission keys (settings pages, branches…)
   * stay visible, as before; their routes are still guarded. A collapse left
   * with no children is dropped. */
  const pruneSubMenu = (nav: NavigationTree): NavigationTree | null => {
    if (!nav.subMenu || nav.subMenu.length === 0) return nav;
    const subMenu = nav.subMenu
      .filter((sub) => sub && perm[sub.key] !== false)
      .map(pruneSubMenu)
      .filter((sub): sub is NavigationTree => sub !== null);
    if (subMenu.length === 0) return null;
    return subMenu.length === nav.subMenu.length ? nav : { ...nav, subMenu };
  };

  const permitted = navigationTree
    .filter((nav) => nav && perm[nav.key])
    .map(pruneSubMenu)
    .filter((nav): nav is NavigationTree => nav !== null);

  const isGrouped = permitted.some((nav) => nav.group);

  const renderGrouped = () => {
    const sections = navigationGroups
      .map((group) => ({
        group,
        items: permitted.filter((nav) => nav.group === group.key),
      }))
      .filter((section) => section.items.length > 0);

    const knownGroups = new Set(navigationGroups.map((group) => group.key));
    const ungrouped = permitted.filter(
      (nav) => !nav.group || !knownGroups.has(nav.group)
    );

    return (
      <>
        {sections.map(({ group, items }, index) => {
          const label = t(group.translateKey, group.title);
          return (
            <div
              key={group.key}
              role="group"
              aria-label={label}
              className={classNames(
                "menu-nav-group",
                index === 0 && "menu-nav-group-first"
              )}
            >
              {collapsed ? (
                index > 0 && <div className="menu-nav-group-divider" />
              ) : (
                <div
                  className={classNames("menu-title", `menu-title-${navMode}`)}
                >
                  {label}
                </div>
              )}
              {items.map((nav) => getNavItem(nav))}
            </div>
          );
        })}
        {ungrouped.map((nav) => getNavItem(nav))}
      </>
    );
  };

  return (
    <Menu
      className="px-2.5 py-2.5"
      menuItemHeight={34}
      variant={navMode}
      sideCollapsed={collapsed}
      defaultActiveKeys={activedRoute?.key ? [activedRoute.key] : []}
      defaultExpandedKeys={defaulExpandKey}
    >
      {isGrouped ? renderGrouped() : permitted.map((nav) => getNavItem(nav))}
    </Menu>
  );
};

export default VerticalMenuContent;
