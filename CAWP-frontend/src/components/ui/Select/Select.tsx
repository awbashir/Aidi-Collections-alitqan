import { forwardRef, Ref } from 'react'
import classNames from 'classnames'
import ReactSelect from 'react-select'
import CreatableSelect from 'react-select/creatable'
import AsyncSelect from 'react-select/async'
import { theme } from 'twin.macro'
import isEmpty from 'lodash/isEmpty'
import get from 'lodash/get'
import { useConfig } from '../ConfigProvider'
import { useForm } from '../Form/context'
import { useInputGroup } from '../InputGroup/context'
import { HiCheck, HiChevronDown, HiX } from 'react-icons/hi'
import Spinner from '../Spinner/Spinner'
import { CONTROL_SIZES } from '../utils/constants'
import type { CommonProps, TypeAttributes } from '../@types/common'
import type {
    ControlProps,
    Props as ReactSelectProps,
    GroupBase,
} from 'react-select'
import type { AsyncProps } from 'react-select/async'
import type { CreatableProps } from 'react-select/creatable'
import type { ForwardedRef } from 'react'

interface DefaultOptionProps {
    innerProps: JSX.IntrinsicElements['div']
    label: string
    selectProps: { themeColor?: string }
    isSelected: boolean
    isDisabled: boolean
    isFocused: boolean
}

const DefaultOption = ({
    innerProps,
    label,
    selectProps,
    isSelected,
    isDisabled,
    isFocused,
}: DefaultOptionProps) => {
    const { themeColor } = selectProps
    return (
        <div
            className={classNames(
                'select-option',
                isSelected && 'selected',
                isDisabled && 'disabled',
                isFocused && 'focused'
            )}
            {...innerProps}
        >
            <span className="ml-2">{label}</span>
            {isSelected && (
                <HiCheck
                    className={`text-${themeColor} dark:text-white text-xl`}
                />
            )}
        </div>
    )
}

const DefaultDropdownIndicator = () => {
    return (
        <div className="select-dropdown-indicator">
            <HiChevronDown />
        </div>
    )
}

interface DefaultClearIndicatorProps {
    innerProps: JSX.IntrinsicElements['div']
    ref: Ref<HTMLElement>
}

const DefaultClearIndicator = ({
    innerProps: { ref, ...restInnerProps },
}: DefaultClearIndicatorProps) => {
    return (
        <div {...restInnerProps} ref={ref}>
            <div className="select-clear-indicator">
                <HiX />
            </div>
        </div>
    )
}

interface DefaultLoadingIndicatorProps {
    selectProps: { themeColor?: string }
}

const DefaultLoadingIndicator = ({
    selectProps,
}: DefaultLoadingIndicatorProps) => {
    const { themeColor } = selectProps
    return (
        <Spinner className={`select-loading-indicatior text-${themeColor}`} />
    )
}

export interface SelectProps<
    Option,
    IsMulti extends boolean = false,
    Group extends GroupBase<Option> = GroupBase<Option>
> extends CommonProps,
        ReactSelectProps<Option, IsMulti, Group>,
        AsyncProps<Option, IsMulti, Group>,
        CreatableProps<Option, IsMulti, Group> {
    size?: TypeAttributes.ControlSize
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    field?: any
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    form?: any
    componentAs?: ReactSelect | CreatableSelect | AsyncSelect
}

function _Select<
    Option,
    IsMulti extends boolean = false,
    Group extends GroupBase<Option> = GroupBase<Option>
>(
    props: SelectProps<Option, IsMulti, Group>,
    ref: ForwardedRef<ReactSelect | CreatableSelect | AsyncSelect>
) {
    const {
        size,
        style,
        className,
        form,
        field,
        components,
        componentAs: Component = ReactSelect,
        ...rest
    } = props

    const { controlSize, mode } = useConfig()
    const formControlSize = useForm()?.size
    const inputGroupSize = useInputGroup()?.size

    const selectSize = size || inputGroupSize || formControlSize || controlSize

    const twHeight = theme`height`

    /* Aidi Enterprise field tokens — fixed, not derived from the theme colour:
       rest #B8C4CE, focus #2F5FD6 + a 2px rgba(47,95,214,.22) halo. */
    const AIDI = {
        borderStrong: '#B8C4CE',
        borderStrongDark: '#3A4179',
        focus: '#2F5FD6',
        focusDark: '#7D9DEA',
        focusHalo: '0 0 0 2px rgba(47,95,214,.22)',
        danger: '#B3261E',
        dangerHalo: '0 0 0 2px rgba(179,38,30,.18)',
        navy: '#070A4B',
        azureTint: '#EEF3FD',
        azureTint2: '#D9E4FA',
        body: '#2E3450',
    }

    let isInvalid = false

    if (!isEmpty(form)) {
        const { touched, errors } = form

        const touchedField = get(touched, field.name)
        const errorField = get(errors, field.name)

        isInvalid = touchedField && errorField
    }

    const getBoxShadow = (state: ControlProps<Option, IsMulti, Group>) => {
        if (isInvalid) {
            return AIDI.dangerHalo
        }

        if (state.isFocused) {
            return AIDI.focusHalo
        }

        return 'none'
    }

    const selectClass = classNames('select', `select-${selectSize}`, className)

    return (
        <Component<Option, IsMulti, Group>
            ref={ref}
            className={selectClass}
            classNamePrefix={'select'}
            styles={{
                control: (provided, state) => {
                    return {
                        ...provided,
                        height: twHeight[CONTROL_SIZES[selectSize]],
                        minHeight: twHeight[CONTROL_SIZES[selectSize]],
                        '&:hover': {
                            boxShadow: getBoxShadow(state),
                            cursor: 'pointer',
                        },
                        boxShadow: getBoxShadow(state),
                        borderRadius: '6px',
                        borderColor: isInvalid
                            ? AIDI.danger
                            : state.isFocused
                            ? mode === 'dark'
                                ? AIDI.focusDark
                                : AIDI.focus
                            : mode === 'dark'
                            ? AIDI.borderStrongDark
                            : AIDI.borderStrong,
                    }
                },
                input: (css) => {
                    return {
                        ...css,
                        input: {
                            outline: 'none',
                            outlineOffset: 0,
                            boxShadow: 'none !important',
                        },
                    }
                },
                menu: (provided) => ({ ...provided, zIndex: 50 }),
                ...style,
            }}
            theme={(theme) => ({
                ...theme,
                colors: {
                    ...theme.colors,
                    neutral20:
                        mode === 'dark'
                            ? AIDI.borderStrongDark
                            : AIDI.borderStrong,
                    neutral30:
                        mode === 'dark'
                            ? AIDI.borderStrongDark
                            : AIDI.borderStrong,
                    neutral80: mode === 'dark' ? '#C7CDE4' : AIDI.body,
                    neutral10:
                        mode === 'dark'
                            ? AIDI.borderStrongDark
                            : AIDI.borderStrong,
                    primary25: AIDI.azureTint,
                    primary50: AIDI.azureTint2,
                    primary: AIDI.navy,
                },
            })}
            themeColor={'navy-600'}
            components={{
                IndicatorSeparator: () => null,
                Option: DefaultOption,
                LoadingIndicator: DefaultLoadingIndicator,
                DropdownIndicator: DefaultDropdownIndicator,
                ClearIndicator: DefaultClearIndicator,
                ...components,
            }}
            {...field}
            {...rest}
        />
    )
}

const Select = forwardRef(_Select) as <
    Option,
    IsMulti extends boolean = false,
    Group extends GroupBase<Option> = GroupBase<Option>
>(
    props: SelectProps<Option, IsMulti, Group> & {
        ref?: ForwardedRef<ReactSelect | CreatableSelect | AsyncSelect>
    }
) => ReturnType<typeof _Select>

export default Select
