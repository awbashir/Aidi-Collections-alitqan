import { useEffect } from 'react'
import { THEME_ENUM } from '@/constants/theme.constant'
import { setMode, useAppSelector, useAppDispatch } from '@/store'
import type { Mode } from '@/@types/theme'

function useDarkMode(): [
    isEnabled: boolean,
    onModeChange: (mode: Mode) => void
] {
    const mode = useAppSelector((state) => state.theme.mode)
    const { MODE_DARK, MODE_LIGHT } = THEME_ENUM

    const isEnabled = mode === MODE_DARK

    const dispatch = useAppDispatch()
    const onModeChange = (mode: Mode) => {
        dispatch(setMode(mode))
        // Save to localStorage
        localStorage.setItem('theme-mode', mode)
    }

     // Initialize theme from localStorage on mount
    useEffect(() => {
        const savedMode = localStorage.getItem('theme-mode') as Mode
        if (savedMode && (savedMode === MODE_DARK || savedMode === MODE_LIGHT)) {
            dispatch(setMode(savedMode))
        }
    }, [dispatch, MODE_DARK, MODE_LIGHT])

    useEffect(() => {
        if (window === undefined) {
            return
        }
        const root = window.document.documentElement
        root.classList.remove(isEnabled ? MODE_LIGHT : MODE_DARK)
        root.classList.add(isEnabled ? MODE_DARK : MODE_LIGHT)
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [isEnabled])

    return [isEnabled, onModeChange]
}

export default useDarkMode
