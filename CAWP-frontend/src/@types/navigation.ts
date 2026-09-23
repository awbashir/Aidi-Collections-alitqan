export interface NavigationTree {
    key: string
    path: string
    isExternalLink?: boolean
    title: string
    translateKey: string
    icon: string
    type: 'title' | 'collapse' | 'item'
    authority: string[]
    subMenu: NavigationTree[]
    /** Sidebar group (a `NavigationGroup.key`). Top-level items only. Items
     *  without a group render after the grouped sections, as before. */
    group?: string
}

/** A titled sidebar section. Groups are a presentation layer only: the item
 *  `key`s inside them are still the permission keys the side nav filters on. */
export interface NavigationGroup {
    key: string
    title: string
    translateKey: string
}
