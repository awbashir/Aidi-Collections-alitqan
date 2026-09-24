import { useEffect } from 'react'
// eslint-disable-next-line import/no-named-as-default
import i18n from 'i18next'
import dayjs from 'dayjs'
import { dateLocales } from '@/locales'
import { setDirection, useAppDispatch, useAppSelector } from '@/store'

function useLocale() {
    const locale = useAppSelector((state) => state.locale.currentLang)
    const direction = useAppSelector((state) => state.theme.direction)
    const dispatch = useAppDispatch()

    // The direction always follows the language. They were persisted separately
    // (`theme-direction` in localStorage vs the persisted store), so a store reset after
    // a sign-out could leave Arabic text laid out left-to-right.
    useEffect(() => {
        const wanted = locale === 'ar' ? 'rtl' : 'ltr'
        if (direction !== wanted) {
            dispatch(setDirection(wanted))
            localStorage.setItem('theme-direction', wanted)
        }
    }, [locale, direction, dispatch])

    useEffect(() => {
        const formattedLang = locale.replace(/-([a-z])/g, function (g) {
            return g[1].toUpperCase()
        })
        if (locale !== i18n.language) {
            i18n.changeLanguage(formattedLang)
        }
        dateLocales[formattedLang]().then(() => {
            dayjs.locale(formattedLang)
        })
    }, [locale])

    return locale
}

export default useLocale
