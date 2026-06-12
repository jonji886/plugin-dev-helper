# 方案导出JSON

> 来源：https://open.kujiale.com/pub/saas/open-platform/doc-detail?app_id=15&tree_tab=a&doc_tab=doc&node_id=1512&node_type=1
> 更新时间：2024-07-04 13:47:46

---

### 描述

假设您已了解酷家乐 [定制行业工具的导出JSON](https://www.kujiale.com/hc/article/3FO4K4VW60P2)，同样在酷家乐小程序中提供了方案/模型JSON用于完整的描述方案和模型。

| JSON类型 | 说明 | 接口文档 | 备注 |
|----------|------|----------|------|
| 方案JSON | 酷家乐定制行业方案数据 | [getDesignFullJsonUrlAsync](https://manual.kujiale.com/idp-sdk/latest/apis/idp-sdk.idp.custom.design.export.getdesignfulljsonurlasync) | 接口返回的是文件URL，需要在UI层JS中用fetch API 读取文件内容，比如：`fetch('https://custommodel-oss.kujiale.com/businessproductiondata/2023/06/09/ZILEkAqaZpgAAQAAAA0.gzip').then(res =>{res.json().then(data =>console.log(data))})` |
| 模型JSON | 酷家乐定制模型数据，数据是上面接口的子集 | [getModelJsonAsyncV2](https://manual.kujiale.com/idp-sdk/latest/apis/idp-sdk.idp.custom.design.export.getmodeljsonasyncv2) | 同上 |

### 使用规范

- **非必要不使用导出JSON**：生成JSON耗时可能会久（和方案/模型复杂度挂钩），所以比如想要获取某个模型的名称/尺寸等常见参数信息，完全可以通过其他接口，比如 [getCustomModelByModelIdAsync](https://manual.kujiale.com/idp-sdk/latest/apis/idp-sdk.idp.custom.design.custommodel.getcustommodelbymodelidasync) 获取
- **严禁高并发调用JSON接口**：最高并发数不建议超过 3
- **尽可能异步调用不阻塞主线程UI**：如若存在强依赖建议使用 loading组件

### 示例代码

批量并发获取3个定制工具线(厨卫/定制/门窗)的方案导出JSON：

```javascript
(async () => {
  const [cabinetData, wardrobeData, doorWindowData] = await Promise.all([
    IDP.Custom.Design.Export.getDesignFullJsonUrlAsync({ 
      toolType: IDP.Custom.Common.ToolType.Cabinet 
    }), //厨卫行业线
    IDP.Custom.Design.Export.getDesignFullJsonUrlAsync({ 
      toolType: IDP.Custom.Common.ToolType.Wardrobe 
    }), //全屋定制行业线
    IDP.Custom.Design.Export.getDesignFullJsonUrlAsync({ 
      toolType: IDP.Custom.Common.ToolType.DoorWindow 
    }), //门窗定制行业线
  ])
  console.log('cabinetData', cabinetData)
  console.log('wardrobeData', wardrobeData)
  console.log('doorWindowData', doorWindowData)
})()
```

```javascript
/**
 * 获取当前所在工具线的方案JSON数据
 */
IDP.Custom.Design.Export.getDesignFullJsonUrlAsync().then(res => {
  console.log(res)
})

/**
 * 获取[厨卫]工具线的方案JSON数据
 */
IDP.Custom.Design.Export.getDesignFullJsonUrlAsync({
  toolType: IDP.Custom.Common.ToolType.Cabinet
}).then(res => {
  console.log(res)
})

/**
 * 获取[全屋定制]工具线的方案JSON数据
 */
IDP.Custom.Design.Export.getDesignFullJsonUrlAsync({
  toolType: IDP.Custom.Common.ToolType.Wardrobe
}).then(res => {
  console.log(res)
})

/**
 * 获取[门窗定制]工具线的方案JSON数据
 */
IDP.Custom.Design.Export.getDesignFullJsonUrlAsync({
  toolType: IDP.Custom.Common.ToolType.DoorWindow
}).then(res => {
  console.log(res)
})

/**
 * 根据templateId 指定输出精简JSON的字段（templateId需提前联系酷家乐对接人去创建）
 */
IDP.Custom.Design.Export.getDesignFullJsonUrlAsync({templateId: '3FO4K4VYBR8P'}).then(res => {})
```
